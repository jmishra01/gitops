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


@dataclass(frozen=True)
class FileStatus:
    path: str
    index: str  # staged status letter, " " if none
    worktree: str  # unstaged status letter, " " if none
    orig_path: str | None = None

    @property
    def is_staged(self) -> bool:
        return self.index not in (" ", "?")

    @property
    def is_unstaged(self) -> bool:
        return self.worktree != " "


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
