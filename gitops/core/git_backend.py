"""Thin wrapper around the git CLI.

The CLI is used (rather than a library binding) so that the user's own git
config, credential helpers, SSH setup and hooks all behave exactly as they do
in a terminal. A faster pygit2-based reader can later be slotted in behind the
same methods without touching callers.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

from gitops.core.models import Branch, Commit, FileStatus

# Record separator (\x1e) starts each commit; unit separator (\x1f) splits fields.
COMMIT_FORMAT = "%x1e%H%x1f%h%x1f%an%x1f%ae%x1f%at%x1f%P%x1f%s"
RS = "\x1e"
US = "\x1f"

_BASE_ARGS = ("-c", "core.quotepath=off", "-c", "color.ui=never")


class GitError(RuntimeError):
    def __init__(self, args: Sequence[str], returncode: int, stderr: str):
        self.git_args = list(args)
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"git {' '.join(args)} failed ({returncode}): {self.stderr}")


def parse_commit_header(line: str) -> Commit:
    """Parse one line produced by COMMIT_FORMAT (leading RS optional)."""
    sha, short, author, email, ts, parents, subject = line.lstrip(RS).split(US, 6)
    return Commit(
        sha=sha,
        short_sha=short,
        author=author,
        email=email,
        timestamp=int(ts),
        subject=subject,
        parents=tuple(parents.split()) if parents else (),
    )


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

    def log(self, rev: str = "HEAD", max_count: int = 200, skip: int = 0, all_refs: bool = False) -> list[Commit]:
        if not self.has_commits():
            return []
        args = ["log", f"--format={COMMIT_FORMAT}", f"--max-count={max_count}", f"--skip={skip}"]
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
