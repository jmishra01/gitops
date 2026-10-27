"""Every commit in which a single file was created, modified, renamed or deleted."""

from __future__ import annotations

from gitops.core.git_backend import COMMIT_FORMAT, RS, GitBackend, parse_commit_header
from gitops.core.models import ChangeKind, Commit, FileHistoryEntry


def file_history(
    backend: GitBackend,
    path: str,
    *,
    follow: bool = True,
    all_refs: bool = False,
    max_count: int | None = None,
) -> list[FileHistoryEntry]:
    """Return the history of `path`, newest first.

    With `follow`, history continues across renames (git log --follow), so a
    file moved from `login.py` to `src/auth.py` shows its whole lineage.
    """
    if not backend.has_commits():
        return []

    base = ["log", f"--format={COMMIT_FORMAT}", "-M"]
    if follow:
        base.append("--follow")
    if all_refs:
        base.append("--all")
    if max_count is not None:
        base.append(f"--max-count={max_count}")

    # git silently drops --numstat when combined with --name-status, so the
    # change type and the line counts come from two passes over the same log.
    statuses = _parse_name_status(backend.run(*base, "--name-status", "--", path))
    counts = _parse_numstat(backend.run(*base, "--numstat", "--", path))

    entries: list[FileHistoryEntry] = []
    for commit, status_line in statuses:
        added, removed = counts.get(commit.sha, (None, None))
        if status_line is None:
            # No diff line (e.g. a merge commit) - still part of the file's history.
            entries.append(FileHistoryEntry(commit, ChangeKind.MODIFIED, path, None, added, removed))
            continue
        fields = status_line.split("\t")
        kind = ChangeKind.from_status_letter(fields[0])
        if kind in (ChangeKind.RENAMED, ChangeKind.COPIED) and len(fields) >= 3:
            entries.append(FileHistoryEntry(commit, kind, fields[2], fields[1], added, removed))
        else:
            entries.append(FileHistoryEntry(commit, kind, fields[-1], None, added, removed))
    return entries


def _parse_name_status(out: str) -> list[tuple[Commit, str | None]]:
    result: list[tuple[Commit, str | None]] = []
    current: Commit | None = None
    status: str | None = None
    for line in out.split("\n"):
        if line.startswith(RS):
            if current is not None:
                result.append((current, status))
            current, status = parse_commit_header(line), None
        elif line and status is None and current is not None:
            status = line
    if current is not None:
        result.append((current, status))
    return result


def _parse_numstat(out: str) -> dict[str, tuple[int | None, int | None]]:
    result: dict[str, tuple[int | None, int | None]] = {}
    sha: str | None = None
    for line in out.split("\n"):
        if line.startswith(RS):
            sha = parse_commit_header(line).sha
        elif line and sha is not None and sha not in result:
            added, removed, _ = line.split("\t", 2)
            # Binary files report "-" for both counts.
            result[sha] = (
                int(added) if added != "-" else None,
                int(removed) if removed != "-" else None,
            )
    return result


def version_content(backend: GitBackend, entry: FileHistoryEntry) -> bytes | None:
    """The file's content as of `entry`; for a deletion, the last content before it."""
    if entry.kind is ChangeKind.DELETED:
        proc = backend.run_raw("show", f"{entry.commit.sha}^:{entry.path}")
        return proc.stdout if proc.returncode == 0 else None
    return backend.show_file(entry.commit.sha, entry.path)


def working_copy(backend: GitBackend, path: str) -> bytes | None:
    target = backend.root / path
    return target.read_bytes() if target.is_file() else None
