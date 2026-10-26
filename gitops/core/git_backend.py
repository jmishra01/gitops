"""Thin wrapper around the git CLI.

The CLI is used (rather than a library binding) so that the user's own git
config, credential helpers, SSH setup and hooks all behave exactly as they do
in a terminal. A faster pygit2-based reader can later be slotted in behind the
same methods without touching callers.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

from gitops.core.models import Branch, Commit, FileStatus

# Record separator (\x1e) starts each commit; unit separator (\x1f) splits fields.
COMMIT_FORMAT = "%x1e%H%x1f%h%x1f%an%x1f%ae%x1f%at%x1f%P%x1f%D%x1f%s"
RS = "\x1e"
US = "\x1f"

_BASE_ARGS = ("-c", "core.quotepath=off", "-c", "color.ui=never")
# Network commands must never block on a terminal prompt the GUI can't answer.
_NETWORK_ENV = {"GIT_TERMINAL_PROMPT": "0"}
_PROGRESS_SPLIT = re.compile(rb"[\r\n]")


class GitError(RuntimeError):
    def __init__(self, args: Sequence[str], returncode: int, stderr: str):
        self.git_args = list(args)
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {self.stderr}")


def parse_commit_header(line: str) -> Commit:
    """Parse one line produced by COMMIT_FORMAT (leading RS optional)."""
    sha, short, author, email, ts, parents, refs, subject = line.lstrip(RS).split(US, 7)
    return Commit(
        sha=sha,
        short_sha=short,
        author=author,
        email=email,
        timestamp=int(ts),
        subject=subject,
        parents=tuple(parents.split()) if parents else (),
        refs=tuple(refs.split(", ")) if refs else (),
    )


def _progress_lines(proc: subprocess.Popen[bytes], args: Sequence[str]) -> Iterator[str]:
    """Yield git's progress output (which redraws lines with \\r) one update at a time."""
    assert proc.stdout is not None
    tail: list[str] = []
    buf = b""
    finished = False
    try:
        while chunk := proc.stdout.read1(4096):
            buf += chunk
            *complete, buf = _PROGRESS_SPLIT.split(buf)
            for raw in complete:
                if text := raw.decode("utf-8", errors="replace").strip():
                    tail = (tail + [text])[-20:]
                    yield text
        if text := buf.decode("utf-8", errors="replace").strip():
            tail.append(text)
            yield text
        finished = True
    finally:
        if not finished:
            proc.kill()
        proc.stdout.close()
        returncode = proc.wait()
    if returncode != 0:
        raise GitError(args, returncode, "\n".join(tail))


class GitBackend:
    def __init__(self, repo_path: str | os.PathLike[str], git_executable: str = "git"):
        self.git_executable = git_executable
        self.repo_path = Path(repo_path)
        self.root = Path(self.run("rev-parse", "--show-toplevel").strip())

    @staticmethod
    def is_repo(path: str | os.PathLike[str]) -> bool:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    @staticmethod
    def init(path: str | os.PathLike[str]) -> GitBackend:
        Path(path).mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
        return GitBackend(path)

    @staticmethod
    def clone(url: str, dest: str | os.PathLike[str]) -> Iterator[str]:
        """Clone with progress. Iterate to completion; then open `dest`."""
        args = ["clone", "--progress", url, str(dest)]
        proc = subprocess.Popen(
            ["git", *_BASE_ARGS, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, **_NETWORK_ENV},
        )
        yield from _progress_lines(proc, args)

    # ------------------------------------------------------------------ running

    def _cmd(self, args: Sequence[str]) -> list[str]:
        return [self.git_executable, "-C", str(self.repo_path), *_BASE_ARGS, *args]

    def run(self, *args: str, check: bool = True, input: bytes | None = None) -> str:
        return self.run_bytes(*args, check=check, input=input).decode("utf-8", errors="replace")

    def run_bytes(self, *args: str, check: bool = True, input: bytes | None = None) -> bytes:
        result = self.run_raw(*args, input=input)
        if check and result.returncode != 0:
            raise GitError(args, result.returncode, result.stderr.decode("utf-8", errors="replace"))
        return result.stdout

    def run_raw(self, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        """Run without raising, for commands whose exit code carries meaning."""
        return subprocess.run(self._cmd(args), capture_output=True, input=input)

    def stream(self, *args: str) -> Iterator[str]:
        """Yield stdout lines as git produces them.

        Closing the generator early (e.g. the user cancels a search) kills the
        git process, so long-running history walks stop promptly.
        """
        proc = subprocess.Popen(
            self._cmd(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdout is not None
        finished = False
        try:
            for raw in proc.stdout:
                yield raw.decode("utf-8", errors="replace").rstrip("\n")
            finished = True
        finally:
            if not finished:
                proc.kill()
            proc.stdout.close()
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            if proc.stderr:
                proc.stderr.close()
            returncode = proc.wait()
        if returncode != 0:
            raise GitError(args, returncode, stderr)

    def run_progress(self, *args: str) -> Iterator[str]:
        """Run a network command, yielding its progress messages as they update."""
        proc = subprocess.Popen(
            self._cmd(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, **_NETWORK_ENV},
        )
        yield from _progress_lines(proc, args)

    # ------------------------------------------------------------------ queries

    def current_branch(self) -> str | None:
        name = self.run("rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
        return None if name in ("", "HEAD") else name

    def has_commits(self) -> bool:
        return self.run_bytes("rev-parse", "--verify", "-q", "HEAD", check=False).strip() != b""

    def branches(self) -> list[Branch]:
        out = self.run(
            "for-each-ref",
            f"--format=%(refname){US}%(objectname){US}%(HEAD){US}%(upstream:short)",
            "refs/heads",
            "refs/remotes",
        )
        result: list[Branch] = []
        for line in out.split("\n"):
            if not line:
                continue
            ref, sha, head, upstream = line.split(US)
            if ref.startswith("refs/remotes/") and ref.endswith("/HEAD"):
                continue
            is_remote = ref.startswith("refs/remotes/")
            name = ref.removeprefix("refs/remotes/" if is_remote else "refs/heads/")
            result.append(
                Branch(
                    name=name,
                    is_remote=is_remote,
                    is_current=head == "*",
                    target_sha=sha,
                    upstream=upstream or None,
                )
            )
        return result

    def remotes(self) -> list[str]:
        return [r for r in self.run("remote").split("\n") if r]

    def ahead_behind(self) -> tuple[int, int] | None:
        """(ahead, behind) relative to the upstream, or None without one."""
        proc = self.run_raw("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
        if proc.returncode != 0:
            return None
        ahead, behind = proc.stdout.split()
        return int(ahead), int(behind)

    def status(self) -> list[FileStatus]:
        out = self.run("status", "--porcelain=v1", "-z", "--untracked-files=all")
        parts = out.split("\0")
        result: list[FileStatus] = []
        i = 0
        while i < len(parts):
            entry = parts[i]
            i += 1
            if len(entry) < 4:
                continue
            x, y, path = entry[0], entry[1], entry[3:]
            orig = None
            if x in "RC":
                orig = parts[i]
                i += 1
            result.append(FileStatus(path=path, index=x, worktree=y, orig_path=orig))
        return result

    def log(
        self,
        rev: str = "HEAD",
        max_count: int = 200,
        skip: int = 0,
        all_refs: bool = False,
    ) -> list[Commit]:
        if not self.has_commits():
            return []
        # --date-order never shows a parent before its children, which the graph layout relies on.
        args = ["log", "--date-order", f"--format={COMMIT_FORMAT}", f"--max-count={max_count}", f"--skip={skip}"]
        args += ["--all"] if all_refs else [rev]
        out = self.run(*args)
        return [parse_commit_header(line) for line in out.split("\n") if line.startswith(RS)]

    def commit_diff(self, sha: str, *paths: str) -> str:
        """Patch introduced by `sha`, optionally limited to `paths`."""
        return self.run(
            "show", "-M", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", sha, "--", *paths
        )

    def show_file(self, sha: str, path: str) -> bytes:
        """Return the file's content as it was at `sha`."""
        return self.run_bytes("show", f"{sha}:{path}")

    def last_commit_message(self) -> str:
        return self.run("log", "-1", "--format=%B").rstrip("\n") if self.has_commits() else ""

    # ------------------------------------------------------------------ working tree

    def file_diff(self, path: str, staged: bool) -> str:
        """Diff of one file: index vs worktree, or HEAD vs index when `staged`.

        Prefixes and textconv are pinned so the output can be fed back to
        `git apply` for hunk and line staging.
        """
        args = ["diff", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/"]
        if staged:
            args.append("--cached")
        return self.run(*args, "--", path)

    def untracked_preview(self, path: str) -> str:
        data = (self.root / path).read_bytes()
        if b"\0" in data[:8000]:
            return f"New binary file {path}"
        lines = data.decode("utf-8", errors="replace").splitlines()
        return f"New file {path}\n\n" + "\n".join("+" + line for line in lines)

    def stage(self, *paths: str) -> None:
        self.run("add", "-A", "--", *paths)

    def unstage(self, *paths: str) -> None:
        if self.has_commits():
            self.run("restore", "--staged", "--", *paths)
        else:
            self.run("rm", "--cached", "-r", "-q", "--", *paths)

    def stage_all(self) -> None:
        self.run("add", "-A")

    def unstage_all(self) -> None:
        if self.has_commits():
            self.run("reset", "-q")
        else:
            self.run("rm", "--cached", "-r", "-q", ".")

    def apply_to_index(self, patch: str, reverse: bool = False) -> None:
        """Stage (or with `reverse`, unstage) a partial patch built by gitops.core.patch."""
        args = ["apply", "--cached", "--recount", "--whitespace=nowarn"]
        if reverse:
            args.append("--reverse")
        self.run(*args, "-", input=patch.encode())

    def discard(self, *paths: str) -> None:
        """Throw away unstaged changes to tracked files. Irreversible: confirm in the UI first."""
        self.run("restore", "--worktree", "--", *paths)

    def delete_untracked(self, *paths: str) -> None:
        """Delete untracked files. Irreversible: confirm in the UI first."""
        self.run("clean", "-f", "-q", "--", *paths)

    def commit(self, message: str, amend: bool = False, allow_empty: bool = False) -> str:
        args = ["commit", "-q", "-F", "-"]
        if amend:
            args.append("--amend")
        if allow_empty:
            args.append("--allow-empty")
        self.run(*args, input=message.encode())
        return self.run("rev-parse", "HEAD").strip()

    # ------------------------------------------------------------------ branches

    def checkout(self, branch: str) -> None:
        self.run("switch", "-q", branch)

    def checkout_remote(self, remote_branch: str) -> str:
        """Create a local tracking branch for e.g. 'origin/feature' and switch to it."""
        local = remote_branch.split("/", 1)[1]
        self.run("switch", "-q", "--track", "-c", local, remote_branch)
        return local

    def create_branch(self, name: str, start: str = "HEAD", checkout: bool = True) -> None:
        if checkout:
            self.run("switch", "-q", "-c", name, start)
        else:
            self.run("branch", name, start)

    def rename_branch(self, old: str, new: str) -> None:
        self.run("branch", "-m", old, new)

    def delete_branch(self, name: str, force: bool = False) -> None:
        self.run("branch", "-D" if force else "-d", name)

    def is_valid_branch_name(self, name: str) -> bool:
        return bool(name) and self.run_raw("check-ref-format", "--branch", name).returncode == 0

    # ------------------------------------------------------------------ remotes

    def fetch(self, remote: str | None = None) -> Iterator[str]:
        args = ["fetch", "--progress", "--prune"]
        args += [remote] if remote else ["--all"]
        return self.run_progress(*args)

    def pull(self) -> Iterator[str]:
        return self.run_progress("pull", "--progress")

    def push(self, remote: str | None = None, branch: str | None = None, force_with_lease: bool = False) -> Iterator[str]:
        """Push the current branch; sets the upstream when it has none."""
        args = ["push", "--progress"]
        if force_with_lease:
            args.append("--force-with-lease")
        branch = branch or self.current_branch()
        has_upstream = self.run_raw("rev-parse", "--abbrev-ref", "@{upstream}").returncode == 0
        if not has_upstream and branch:
            remotes = self.remotes()
            target = remote or ("origin" if "origin" in remotes else (remotes[0] if remotes else None))
            if target is None:
                raise GitError(["push"], 1, "This repository has no remote to push to.")
            args += ["-u", target, branch]
        return self.run_progress(*args)
