from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ChangeKind(str, Enum):
    CREATED = "Created"
    MODIFIED = "Modified"
    RENAMED = "Renamed"
    COPIED = "Copied"
    DELETED = "Deleted"
    TYPE_CHANGED = "Type changed"
    # Content-search classifications
    ADDED = "Added"
    CHANGED = "Changed"
    REMOVED = "Removed"

    @classmethod
    def from_status_letter(cls, letter: str) -> ChangeKind:
        return {
            "A": cls.CREATED,
            "M": cls.MODIFIED,
            "R": cls.RENAMED,
            "C": cls.COPIED,
            "D": cls.DELETED,
            "T": cls.TYPE_CHANGED,
        }.get(letter[:1], cls.MODIFIED)


@dataclass(frozen=True)
class Commit:
    sha: str
    short_sha: str
    author: str
    email: str
    timestamp: int
    subject: str
    parents: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()  # e.g. ("HEAD -> main", "origin/main", "tag: v1.0")

    @property
    def date(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).astimezone()

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


@dataclass(frozen=True)
class Branch:
    name: str
    is_remote: bool
    is_current: bool
    target_sha: str
    upstream: str | None = None
    ahead: int = 0  # commits not on the upstream yet
    behind: int = 0  # upstream commits not here yet
    upstream_gone: bool = False  # the upstream branch was deleted on the remote
    subject: str = ""  # tip commit
    author: str = ""
    timestamp: int = 0

    @property
    def date(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).astimezone()


@dataclass(frozen=True)
class FileStatus:
    path: str
    index: str  # staged status letter, " " if none
    worktree: str  # unstaged status letter, " " if none
    orig_path: str | None = None

    # Porcelain XY pairs that mean "unmerged" during a merge, rebase or stash apply.
    CONFLICT_CODES = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})

    @property
    def is_conflicted(self) -> bool:
        return self.index + self.worktree in self.CONFLICT_CODES

    @property
    def is_staged(self) -> bool:
        return self.index not in (" ", "?") and not self.is_conflicted

    @property
    def is_unstaged(self) -> bool:
        return self.worktree != " " or self.is_conflicted


@dataclass(frozen=True)
class Stash:
    ref: str  # "stash@{0}"
    sha: str
    timestamp: int
    message: str

    @property
    def date(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).astimezone()


@dataclass(frozen=True)
class Tag:
    name: str
    target_sha: str  # the commit the tag points at
    annotated: bool
    message: str = ""


@dataclass(frozen=True)
class FileHistoryEntry:
    commit: Commit
    kind: ChangeKind
    path: str  # path of the file as of this commit
    old_path: str | None = None  # set for renames/copies
    added: int | None = None  # None for binary files
    removed: int | None = None


@dataclass
class SearchHit:
    commit: Commit
    path: str
    kind: ChangeKind  # ADDED, CHANGED or REMOVED
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)

    @property
    def preview(self) -> str:
        first_removed = self.removed_lines[0].strip() if self.removed_lines else ""
        first_added = self.added_lines[0].strip() if self.added_lines else ""
        if self.kind is ChangeKind.ADDED:
            return f"+{first_added}"
        if self.kind is ChangeKind.REMOVED:
            return f"-{first_removed}"
        return f"-{first_removed}  +{first_added}"


@dataclass(frozen=True)
class BlameLine:
    sha: str
    line_number: int  # 1-based, in the blamed version
    author: str
    timestamp: int
    summary: str
    orig_path: str  # path in the commit that introduced the line (differs after renames)
    text: str

    @property
    def date(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).astimezone()

    @property
    def uncommitted(self) -> bool:
        return self.sha == "0" * 40


@dataclass(frozen=True)
class ReflogEntry:
    sha: str
    selector: str  # e.g. "main@{2}"
    action: str  # e.g. "commit: Add login", "rebase (finish): ...", "reset: moving to HEAD~1"
    timestamp: int

    @property
    def date(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).astimezone()


@dataclass(frozen=True)
class Submodule:
    path: str
    sha: str
    state: str  # "ok", "not initialized", "modified" (checked-out commit differs), "conflict"
    describe: str = ""


@dataclass(frozen=True)
class Worktree:
    path: str
    head: str
    branch: str | None  # None when detached
    is_main: bool
    locked: bool = False
    prunable: bool = False


@dataclass(frozen=True)
class RepoSummary:
    path: str
    name: str
    branch: str | None
    changed: int
    conflicts: int
    ahead_behind: tuple[int, int] | None
    stashes: int
    last_commit: Commit | None
    operation: str | None
    error: str | None = None


@dataclass(frozen=True)
class CommitFile:
    """One file changed by a commit (relative to its first parent)."""

    status: str  # A, M, D, R, C, T
    path: str
    old_path: str | None = None  # for renames and copies
    added: int | None = None  # None for binary files
    removed: int | None = None
