"""Thin wrapper around the git CLI.

The CLI is used (rather than a library binding) so that the user's own git
config, credential helpers, SSH setup and hooks all behave exactly as they do
in a terminal. A faster pygit2-based reader can later be slotted in behind the
same methods without touching callers.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path

from gitops.core.encoding import decode_lossless, encode_lossless
from gitops.core.models import (
    BlameLine,
    Branch,
    Commit,
    CommitFile,
    FileStatus,
    ReflogEntry,
    RepoSummary,
    Stash,
    Submodule,
    Tag,
    Worktree,
)

# Record separator (\x1e) starts each commit; unit separator (\x1f) splits fields.
COMMIT_FORMAT = "%x1e%H%x1f%h%x1f%an%x1f%ae%x1f%at%x1f%P%x1f%D%x1f%s"
RS = "\x1e"
US = "\x1f"

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's well-known empty tree object
# --no-optional-locks: our background refreshes (status, diff) must never take .git/index.lock,
# or a git command the user runs in a terminal at the same moment fails with "index.lock exists".
_BASE_ARGS = ("--no-optional-locks", "-c", "core.quotepath=off", "-c", "color.ui=never")
# Network commands must never block on a terminal prompt the GUI can't answer.
_NETWORK_ENV = {"GIT_TERMINAL_PROMPT": "0"}


def _messages_need_english(environ: Mapping[str, str]) -> bool:
    """Would git print translated (non-English) messages with this environment?"""
    locale = environ.get("LC_ALL") or environ.get("LC_MESSAGES") or environ.get("LANG") or ""
    base = locale.split(".")[0]
    if base in ("", "C", "POSIX"):
        return False  # gettext ignores LANGUAGE under the C locale
    language = environ.get("LANGUAGE", "")
    return not base.startswith("en") or any(part and not part.startswith("en") for part in language.split(":"))


def _english_env(environ: Mapping[str, str]) -> dict[str, str]:
    """`environ` with git's messages forced to English and character handling left alone.

    The UI recognises some outcomes by git's wording ("not fully merged",
    "rejected", ...). LC_ALL is split into LC_CTYPE rather than overridden so
    non-ASCII searches and paths still behave as in the user's locale.
    """
    env = dict(environ)
    lc_all = env.pop("LC_ALL", None)
    if lc_all and "LC_CTYPE" not in env:
        env["LC_CTYPE"] = lc_all
    env.pop("LANGUAGE", None)
    env["LC_MESSAGES"] = "C"
    return env


# Decided once: forcing the locale costs git ~10 ms per call (it loads its
# translations), so it's only done when the user's locale isn't English already.
_BASE_ENV: dict[str, str] | None = _english_env(os.environ) if _messages_need_english(os.environ) else None


def _git_env(extra: dict[str, str] | None = None) -> dict[str, str] | None:
    """Environment for every git process; None means "inherit unchanged" (the fast path)."""
    if not extra:
        return _BASE_ENV
    return {**(_BASE_ENV or os.environ), **extra}


# Every git process runs detached from any terminal: no stdin and a new session,
# so ssh, credential helpers and GPG can't sit waiting on a prompt nobody can
# see (launched from a terminal, they would otherwise grab it and hang). They
# fail fast instead, or use a graphical prompt such as ssh-askpass or pinentry-mac.
_DETACHED = {"start_new_session": True}


def _kill(proc: subprocess.Popen[bytes]) -> None:
    """Stop git and everything it started (ssh, remote helpers) in its session."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError, OSError):  # Windows, or already gone
        proc.kill()
_PROGRESS_SPLIT = re.compile(rb"[\r\n]")
# diff3 markers include the common ancestor, so the conflict editor can offer "use base".
_DIFF3 = ("-c", "merge.conflictStyle=diff3")


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
            _kill(proc)
        proc.stdout.close()
        returncode = proc.wait()
    if returncode != 0:
        raise GitError(args, returncode, "\n".join(tail))


class GitBackend:
    def __init__(self, repo_path: str | os.PathLike[str], git_executable: str = "git"):
        self.git_executable = git_executable
        self.repo_path = Path(repo_path)
        # Called with each git command's arguments (the UI's git console shows them).
        self.command_listeners: list[Callable[[list[str]], None]] = []
        self.root = Path(self.run("rev-parse", "--show-toplevel").strip())

    @staticmethod
    def is_repo(path: str | os.PathLike[str]) -> bool:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=_git_env(),
            **_DETACHED,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    @staticmethod
    def init(path: str | os.PathLike[str]) -> GitBackend:
        Path(path).mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "-q", str(path)], check=True, capture_output=True, stdin=subprocess.DEVNULL, env=_git_env()
        )
        return GitBackend(path)

    @staticmethod
    def clone(url: str, dest: str | os.PathLike[str]) -> Iterator[str]:
        """Clone with progress. Iterate to completion; then open `dest`."""
        args = ["clone", "--progress", url, str(dest)]
        proc = subprocess.Popen(
            ["git", *_BASE_ARGS, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=_git_env(_NETWORK_ENV),
            **_DETACHED,
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

    def run_raw(
        self, *args: str, input: bytes | None = None, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        """Run without raising, for commands whose exit code carries meaning."""
        self._log(args)
        return subprocess.run(
            self._cmd(args),
            capture_output=True,
            input=input,
            stdin=None if input is not None else subprocess.DEVNULL,
            env=_git_env(env),
            **_DETACHED,
        )

    def _log(self, args: Sequence[str]) -> None:
        for listener in self.command_listeners:
            listener(list(args))

    def stream(self, *args: str) -> Iterator[str]:
        """Yield stdout lines as git produces them.

        Closing the generator early (e.g. the user cancels a search) kills the
        git process, so long-running history walks stop promptly.
        """
        self._log(args)
        proc = subprocess.Popen(
            self._cmd(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_env(),
            **_DETACHED,
        )
        assert proc.stdout is not None
        finished = False
        try:
            for raw in proc.stdout:
                yield raw.decode("utf-8", errors="replace").rstrip("\n")
            finished = True
        finally:
            if not finished:
                _kill(proc)
            proc.stdout.close()
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            if proc.stderr:
                proc.stderr.close()
            returncode = proc.wait()
        if returncode != 0:
            raise GitError(args, returncode, stderr)

    def run_progress(self, *args: str) -> Iterator[str]:
        """Run a network command, yielding its progress messages as they update."""
        self._log(args)
        proc = subprocess.Popen(
            self._cmd(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=_git_env(_NETWORK_ENV),
            **_DETACHED,
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

    def _commit_base(self, commit: Commit) -> str:
        """What a commit is compared with: its first parent (so merges show what they brought in), or nothing."""
        return commit.parents[0] if commit.parents else EMPTY_TREE

    def commit_files(self, commit: Commit) -> list[CommitFile]:
        """Files the commit changed, with line counts, in path order."""
        base = self._commit_base(commit)
        status_out = self.run("diff", "--name-status", "-z", "-M", "--no-ext-diff", base, commit.sha)
        parts = status_out.split("\0")
        files: list[CommitFile] = []
        i = 0
        while i < len(parts) and parts[i]:
            code = parts[i]
            if code[:1] in "RC":
                old, new = parts[i + 1], parts[i + 2]
                files.append(CommitFile(code[:1], new, old))
                i += 3
            else:
                files.append(CommitFile(code[:1], parts[i + 1]))
                i += 2
        counts: dict[str, tuple[int | None, int | None]] = {}
        num_out = self.run("diff", "--numstat", "-z", "-M", "--no-ext-diff", "--no-textconv", base, commit.sha)
        records = num_out.split("\0")
        j = 0
        while j < len(records) and records[j]:
            added, removed, path = records[j].split("\t", 2)
            if path == "":  # rename: the old and new paths follow as separate records
                path = records[j + 2]
                j += 3
            else:
                j += 1
            counts[path] = (int(added) if added != "-" else None, int(removed) if removed != "-" else None)
        return [
            CommitFile(f.status, f.path, f.old_path, *counts.get(f.path, (None, None)))
            for f in sorted(files, key=lambda f: f.path)
        ]

    def commit_file_diff(self, commit: Commit, file: CommitFile) -> str:
        """Patch of one file within a commit (lossless text, like file_diff)."""
        paths = [p for p in (file.old_path, file.path) if p]
        return decode_lossless(
            self.run_bytes(
                "diff", "-M", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/",
                self._commit_base(commit), commit.sha, "--", *paths,
            )
        )

    def tracked_files(self) -> list[str]:
        return [p for p in self.run("ls-files", "-z").split("\0") if p]

    def commit_message(self, sha: str) -> str:
        return self.run("log", "-1", "--format=%B", sha).strip()

    def checkout_detached(self, sha: str) -> None:
        """Look at an old commit without moving any branch (detached HEAD)."""
        self.run("switch", "-q", "--detach", sha)

    def show_file(self, sha: str, path: str) -> bytes:
        """Return the file's content as it was at `sha`."""
        return self.run_bytes("show", f"{sha}:{path}")

    def last_commit_message(self) -> str:
        return self.run("log", "-1", "--format=%B").rstrip("\n") if self.has_commits() else ""

    # ------------------------------------------------------------------ working tree

    def file_diff(self, path: str, staged: bool) -> str:
        """Diff of one file: index vs worktree, or HEAD vs index when `staged`.

        Prefixes and textconv are pinned so the output can be fed back to
        `git apply` for hunk and line staging. The text is decoded losslessly
        (see gitops.core.encoding), so non-UTF-8 files round-trip byte for byte;
        pass it through `display_text` before showing it in Qt.
        """
        args = ["diff", "--no-ext-diff", "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/"]
        if staged:
            args.append("--cached")
        return decode_lossless(self.run_bytes(*args, "--", path))

    PREVIEW_BYTES = 512 * 1024

    def untracked_preview(self, path: str) -> str:
        """Preview of a new file, reading at most PREVIEW_BYTES so huge files can't stall the UI."""
        target = self.root / path
        size = target.stat().st_size
        with target.open("rb") as handle:
            data = handle.read(self.PREVIEW_BYTES)
        if b"\0" in data[:8000]:
            return f"New binary file {path} ({size:,} bytes)"
        lines = data.decode("utf-8", errors="replace").splitlines()
        if size > len(data):
            lines = lines[:-1]  # the last line was probably cut mid-way
            note = f"\n\n… showing the first {len(data) // 1024} KB of {size / 1_048_576:.1f} MB"
        else:
            note = ""
        return f"New file {path}\n\n" + "\n".join("+" + line for line in lines) + note

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
        self.run(*args, "-", input=encode_lossless(patch))

    def discard_from_worktree(self, patch: str) -> None:
        """Throw away just the changes in `patch` from the working tree. Irreversible: confirm first.

        `patch` must be built against the working tree (build_partial_patch with
        reverse=True on an index-to-worktree diff) and is applied in reverse.
        """
        self.run("apply", "--reverse", "--recount", "--whitespace=nowarn", "-", input=encode_lossless(patch))

    def change_counts(self) -> tuple[dict[str, tuple[int | None, int | None]], dict[str, tuple[int | None, int | None]]]:
        """Lines added/removed per path: (unstaged, staged). Binary files count as (None, None)."""

        def parse(out: str) -> dict[str, tuple[int | None, int | None]]:
            counts: dict[str, tuple[int | None, int | None]] = {}
            for record in out.split("\0"):
                if not record:
                    continue
                parts = record.split("\t", 2)
                if len(parts) < 3:
                    continue
                added, removed, path = parts
                counts[path] = (int(added) if added != "-" else None, int(removed) if removed != "-" else None)
            return counts

        base = ["diff", "--numstat", "-z", "--no-renames", "--no-ext-diff", "--no-textconv"]
        unstaged = parse(self.run(*base))
        # Before the first commit there's no HEAD, so compare the index with git's empty tree.
        against = [] if self.has_commits() else [EMPTY_TREE]
        staged = parse(self.run(*base, "--cached", *against))
        return unstaged, staged

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

    # ------------------------------------------------------------------ repository state

    def git_path(self, name: str) -> Path:
        """Location of a file inside .git (handles worktrees and $GIT_DIR)."""
        rel = self.run("rev-parse", "--git-path", name).strip()
        return (self.repo_path / rel).resolve()

    def merge_in_progress(self) -> bool:
        return self.git_path("MERGE_HEAD").exists()

    def merge_message(self) -> str:
        path = self.git_path("MERGE_MSG")
        if not path.exists():
            return ""
        lines = path.read_text(errors="replace").splitlines()
        return "\n".join(line for line in lines if not line.startswith("#")).strip()

    def tags(self) -> list[Tag]:
        out = self.run(
            "for-each-ref",
            "--sort=-creatordate",
            f"--format=%(refname:short){US}%(objecttype){US}%(*objectname){US}%(objectname){US}%(contents:subject)",
            "refs/tags",
        )
        result: list[Tag] = []
        for line in out.split("\n"):
            if not line:
                continue
            name, objtype, peeled, sha, subject = line.split(US, 4)
            annotated = objtype == "tag"
            result.append(Tag(name, peeled if annotated else sha, annotated, subject if annotated else ""))
        return result

    # ------------------------------------------------------------------ merge

    def merge(self, branch: str, no_ff: bool = False, message: str | None = None) -> list[str]:
        """Merge `branch` into HEAD. Returns conflicted paths (empty on a clean merge)."""
        args = ["merge", "--no-edit"]
        if no_ff:
            args.append("--no-ff")
        if message:
            args += ["-m", message]
        proc = self.run_raw(*_DIFF3, *args, branch)
        if proc.returncode == 0:
            return []
        conflicts = self.conflicted_paths()
        if conflicts:
            return conflicts
        output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
        raise GitError(args + [branch], proc.returncode, output)

    def merge_abort(self) -> None:
        self.run("merge", "--abort")

    def conflicted_paths(self) -> list[str]:
        return [p for p in self.run("diff", "--name-only", "--diff-filter=U").split("\n") if p]

    def conflict_versions(self, path: str) -> tuple[bytes | None, bytes | None, bytes | None]:
        """(base, ours, theirs) content from index stages 1-3; None where a side doesn't have the file."""

        def stage(n: int) -> bytes | None:
            proc = self.run_raw("show", f":{n}:{path}")
            return proc.stdout if proc.returncode == 0 else None

        return stage(1), stage(2), stage(3)

    def mark_resolved(self, path: str, content: bytes | None) -> None:
        """Write the resolution and stage it; `None` resolves by deleting the file."""
        target = self.root / path
        if content is None:
            self.run("rm", "-q", "--ignore-unmatch", "--", path)
            target.unlink(missing_ok=True)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        self.run("add", "--", path)

    # ------------------------------------------------------------------ stash

    def stashes(self) -> list[Stash]:
        out = self.run("stash", "list", f"--format=%gd{US}%H{US}%ct{US}%gs")
        result: list[Stash] = []
        for line in out.split("\n"):
            if line:
                ref, sha, ts, message = line.split(US, 3)
                result.append(Stash(ref, sha, int(ts), message))
        return result

    def stash_save(self, message: str = "", include_untracked: bool = False, keep_index: bool = False) -> bool:
        """Stash local changes. Returns False when there was nothing to stash."""
        before = len(self.stashes())
        args = ["stash", "push", "-q"]
        if include_untracked:
            args.append("--include-untracked")
        if keep_index:
            args.append("--keep-index")
        if message:
            args += ["-m", message]
        self.run(*args)
        return len(self.stashes()) > before

    def stash_apply(self, ref: str = "stash@{0}", pop: bool = False) -> list[str]:
        """Apply (or pop) a stash. Returns conflicted paths; a conflicted pop keeps the stash."""
        proc = self.run_raw(*_DIFF3, "stash", "pop" if pop else "apply", "--index", ref)
        if proc.returncode != 0 and b"--index" in proc.stderr + proc.stdout:
            # The staged part can't be restored exactly; fall back to a plain apply.
            proc = self.run_raw(*_DIFF3, "stash", "pop" if pop else "apply", ref)
        if proc.returncode == 0:
            return []
        conflicts = self.conflicted_paths()
        if conflicts:
            return conflicts
        raise GitError(["stash", "apply", ref], proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", errors="replace"))

    def stash_drop(self, ref: str) -> None:
        self.run("stash", "drop", "-q", ref)

    def stash_diff(self, ref: str) -> str:
        return self.run("stash", "show", "-p", "--include-untracked", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", ref)

    # ------------------------------------------------------------------ operations in progress

    # Commands that may stop for conflicts; their --continue must never open an editor.
    _NO_EDITOR = {"GIT_EDITOR": "true", "GIT_SEQUENCE_EDITOR": "true"}

    def operation_in_progress(self) -> str | None:
        """'merge', 'rebase', 'cherry-pick' or 'revert' when one is stopped mid-way."""
        if self.git_path("rebase-merge").exists() or self.git_path("rebase-apply").exists():
            return "rebase"
        if self.git_path("CHERRY_PICK_HEAD").exists():
            return "cherry-pick"
        if self.git_path("REVERT_HEAD").exists():
            return "revert"
        if self.git_path("MERGE_HEAD").exists():
            return "merge"
        return None

    def _stoppable(self, *args: str, env: dict[str, str] | None = None) -> list[str]:
        """Run a command that may stop on conflicts. Returns conflicted paths, raises on other errors."""
        proc = self.run_raw(*_DIFF3, *args, env={**self._NO_EDITOR, **(env or {})})
        if proc.returncode == 0:
            return []
        conflicts = self.conflicted_paths()
        if conflicts:
            return conflicts
        raise GitError(list(args), proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", errors="replace"))

    def continue_operation(self) -> list[str]:
        op = self.operation_in_progress()
        if op is None:
            return []
        if op == "merge":
            self.run("commit", "--no-edit")
            return []
        return self._stoppable(op, "--continue")

    def abort_operation(self) -> None:
        op = self.operation_in_progress()
        if op:
            self.run(op, "--abort")

    def skip_commit(self) -> list[str]:
        """Skip the commit a rebase/cherry-pick stopped on."""
        op = self.operation_in_progress()
        if op not in ("rebase", "cherry-pick", "revert"):
            return []
        return self._stoppable(op, "--skip")

    # ------------------------------------------------------------------ history rewriting

    def cherry_pick(self, *shas: str) -> list[str]:
        return self._stoppable("cherry-pick", *shas)

    def revert(self, sha: str, mainline: int | None = None) -> list[str]:
        args = ["revert", "--no-edit"]
        if mainline:
            args += ["-m", str(mainline)]
        return self._stoppable(*args, sha)

    def reset(self, sha: str, mode: str = "mixed") -> None:
        if mode not in ("soft", "mixed", "hard"):
            raise ValueError(mode)
        self.run("reset", "-q", f"--{mode}", sha)

    def rebase_onto(self, upstream: str) -> list[str]:
        return self._stoppable("rebase", upstream)

    # ------------------------------------------------------------------ tags

    def create_tag(self, name: str, target: str = "HEAD", message: str | None = None) -> None:
        if message:
            self.run("tag", "-a", name, target, "-m", message)
        else:
            self.run("tag", name, target)

    def delete_tag(self, name: str) -> None:
        self.run("tag", "-d", name)

    def push_tag(self, name: str, remote: str = "origin") -> Iterator[str]:
        return self.run_progress("push", "--progress", remote, f"refs/tags/{name}")

    def delete_remote_tag(self, name: str, remote: str = "origin") -> Iterator[str]:
        return self.run_progress("push", "--progress", remote, f":refs/tags/{name}")

    # ------------------------------------------------------------------ blame and commit search

    def blame(self, path: str, rev: str | None = None) -> list[BlameLine]:
        """Who last changed each line of `path` (as of `rev`, or the working tree)."""
        args = ["blame", "--porcelain", "-M", "-C"]
        if rev:
            args.append(rev)
        out = self.run(*args, "--", path)
        commits: dict[str, dict[str, str]] = {}
        lines: list[BlameLine] = []
        current: dict[str, str] | None = None
        sha = ""
        final_line = 0
        for raw in out.split("\n"):
            if raw.startswith("\t"):
                info = commits[sha]
                lines.append(
                    BlameLine(
                        sha=sha,
                        line_number=final_line,
                        author=info.get("author", ""),
                        timestamp=int(info.get("author-time", "0")),
                        summary=info.get("summary", ""),
                        orig_path=info.get("filename", path),
                        text=raw[1:],
                    )
                )
                continue
            parts = raw.split(" ")
            if len(parts) >= 3 and len(parts[0]) == 40 and all(c in "0123456789abcdef" for c in parts[0]):
                sha, final_line = parts[0], int(parts[2])
                current = commits.setdefault(sha, {})
            elif current is not None and parts and parts[0]:
                key, _, value = raw.partition(" ")
                current[key] = value
        return lines

    def search_commits(self, query: str, all_refs: bool = False, max_count: int = 500) -> list[Commit]:
        """Find commits by message text, `author:<name>`, or a (partial) SHA."""
        query = query.strip()
        if not query or not self.has_commits():
            return []
        base = ["log", "--date-order", f"--format={COMMIT_FORMAT}", f"--max-count={max_count}"]
        base.append("--all" if all_refs else "HEAD")
        if query.startswith("author:"):
            args = base + [f"--author={query[7:].strip()}", "--regexp-ignore-case"]
        else:
            args = base + ["--fixed-strings", "--regexp-ignore-case", f"--grep={query}"]
        found = [parse_commit_header(line) for line in self.run(*args).split("\n") if line.startswith(RS)]
        if re.fullmatch(r"[0-9a-fA-F]{4,40}", query):
            proc = self.run_raw("log", "-1", f"--format={COMMIT_FORMAT}", f"{query}^{{commit}}", "--")
            if proc.returncode == 0:
                head = parse_commit_header(proc.stdout.decode("utf-8", errors="replace").strip("\n"))
                found = [head] + [c for c in found if c.sha != head.sha]
        return found

    # ------------------------------------------------------------------ undo via reflog

    def reflog(self, ref: str | None = None, max_count: int = 50) -> list[ReflogEntry]:
        """Where `ref` (default: the current branch, else HEAD) has pointed, newest first."""
        ref = ref or self.current_branch() or "HEAD"
        proc = self.run_raw("reflog", "show", f"--max-count={max_count}", f"--format=%H{US}%gd{US}%gs{US}%ct", ref, "--")
        if proc.returncode != 0:
            return []
        entries: list[ReflogEntry] = []
        for line in proc.stdout.decode("utf-8", errors="replace").split("\n"):
            if line:
                sha, selector, action, ts = line.split(US, 3)
                entries.append(ReflogEntry(sha, selector, action, int(ts)))
        return entries

    def restore_to(self, sha: str) -> None:
        """Move the current branch back to `sha`, keeping uncommitted work.

        Uses `reset --keep`, which refuses (instead of overwriting) when a
        file with local changes would have to change.
        """
        self.run("reset", "-q", "--keep", sha)

    # ------------------------------------------------------------------ submodules and worktrees

    def submodules(self) -> list[Submodule]:
        if not (self.root / ".gitmodules").exists():
            return []
        out = self.run("submodule", "status")
        states = {" ": "ok", "-": "not initialized", "+": "modified", "U": "conflict"}
        result: list[Submodule] = []
        for line in out.split("\n"):
            if not line:
                continue
            flag, rest = line[0], line[1:]
            parts = rest.split(" ", 2)
            describe = parts[2].strip("()") if len(parts) > 2 else ""
            result.append(Submodule(path=parts[1], sha=parts[0], state=states.get(flag, "ok"), describe=describe))
        return result

    def update_submodules(self) -> Iterator[str]:
        return self.run_progress("submodule", "update", "--init", "--recursive", "--progress")

    def worktrees(self) -> list[Worktree]:
        out = self.run("worktree", "list", "--porcelain")
        result: list[Worktree] = []
        for block in out.strip().split("\n\n"):
            if not block.strip():
                continue
            fields: dict[str, str] = {}
            for line in block.split("\n"):
                key, _, value = line.partition(" ")
                fields[key] = value
            branch = fields.get("branch", "").removeprefix("refs/heads/") or None
            result.append(
                Worktree(
                    path=fields.get("worktree", ""),
                    head=fields.get("HEAD", ""),
                    branch=branch,
                    is_main=not result,  # git lists the main worktree first
                    locked="locked" in fields,
                    prunable="prunable" in fields,
                )
            )
        return result

    def add_worktree(self, path: str, branch: str, new_branch: bool = False, start: str = "HEAD") -> None:
        if new_branch:
            self.run("worktree", "add", "-b", branch, path, start)
        else:
            self.run("worktree", "add", path, branch)

    def remove_worktree(self, path: str, force: bool = False) -> None:
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        self.run(*args, path)

    # ------------------------------------------------------------------ ignore rules

    def gitignore_path(self) -> Path:
        return self.root / ".gitignore"

    def read_gitignore(self) -> str:
        """The file's text, decoded losslessly: any bytes and line endings survive a write back."""
        path = self.gitignore_path()
        return decode_lossless(path.read_bytes()) if path.exists() else ""

    def write_gitignore(self, text: str) -> None:
        if text and not text.endswith("\n"):
            text += "\r\n" if "\r\n" in text else "\n"
        self.gitignore_path().write_bytes(encode_lossless(text))

    def add_ignore_rule(self, rule: str) -> None:
        current = self.read_gitignore()
        if rule in current.splitlines():
            return
        eol = "\r\n" if "\r\n" in current else "\n"
        prefix = "" if not current or current.endswith("\n") else eol
        self.write_gitignore(current + prefix + rule + eol)

    def check_ignore(self, path: str) -> tuple[str, int, str] | None:
        """(source file, line, pattern) of the rule ignoring `path`, or None if it isn't ignored."""
        proc = self.run_raw("check-ignore", "-v", "--no-index", "--", path)
        if proc.returncode != 0:
            return None
        info = proc.stdout.decode("utf-8", errors="replace").strip().split("\t", 1)[0]
        source, line, pattern = info.split(":", 2)
        if pattern.startswith("!"):
            return None  # matched a negation: explicitly not ignored
        return source, int(line), pattern

    @staticmethod
    def check_ignore_rules(rules: str, path: str) -> tuple[int, str] | None:
        """(line, pattern) if `rules` (unsaved .gitignore text) would ignore `path`.

        Runs in a throwaway repo so the user's real .gitignore is never touched.
        """
        with tempfile.TemporaryDirectory(prefix="gitops-ignore-") as tmp:
            subprocess.run(
                ["git", "init", "-q", tmp], check=True, capture_output=True, stdin=subprocess.DEVNULL, env=_git_env()
            )
            Path(tmp, ".gitignore").write_text(rules, errors="surrogateescape")
            proc = subprocess.run(
                ["git", "-C", tmp, "check-ignore", "-v", "--no-index", "--", path],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                env=_git_env(),
            )
        if proc.returncode != 0:
            return None
        _, line, pattern = proc.stdout.split("\t", 1)[0].split(":", 2)
        return None if pattern.startswith("!") else (int(line), pattern)

    # ------------------------------------------------------------------ dashboard

    @staticmethod
    def summarize_repo(path: str) -> RepoSummary:
        """Everything the multi-repo dashboard shows, without raising."""
        name = Path(path).name
        try:
            backend = GitBackend(path)
            statuses = backend.status()
            log = backend.log(max_count=1)
            return RepoSummary(
                path=str(backend.root),
                name=backend.root.name,
                branch=backend.current_branch(),
                changed=len(statuses),
                conflicts=sum(1 for s in statuses if s.is_conflicted),
                ahead_behind=backend.ahead_behind(),
                stashes=len(backend.stashes()),
                last_commit=log[0] if log else None,
                operation=backend.operation_in_progress(),
            )
        except (GitError, OSError) as exc:
            return RepoSummary(path, name, None, 0, 0, None, 0, None, None, error=str(exc))

    @staticmethod
    def find_repos(folder: str, max_depth: int = 3) -> list[str]:
        """Git repositories under `folder` (not descending into repos or hidden folders)."""
        found: list[str] = []

        def walk(directory: Path, depth: int) -> None:
            if (directory / ".git").exists():
                found.append(str(directory))
                return
            if depth >= max_depth:
                return
            try:
                children = sorted(p for p in directory.iterdir() if p.is_dir() and not p.name.startswith("."))
            except OSError:
                return
            for child in children:
                walk(child, depth + 1)

        walk(Path(folder).expanduser(), 0)
        return found
