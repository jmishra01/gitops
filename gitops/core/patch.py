"""Hunk- and line-level staging.

A file's diff is parsed into hunks, keeping each line's index in the diff
text so a UI selection maps straight onto it. `build_partial_patch` then
produces a patch containing only the selected changes, which
`GitBackend.apply_to_index` applies to the index.

Staging (diff is index -> worktree, applied forward):
  unselected '-' lines become context, unselected '+' lines are dropped.
Unstaging (diff is HEAD -> index, applied with --reverse):
  unselected '+' lines become context, unselected '-' lines are dropped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass
class Hunk:
    header_index: int
    old_start: int
    new_start: int
    section: str  # text after the second @@ (function context)
    lines: list[tuple[int, str]] = field(default_factory=list)  # (index in diff text, raw line)

    @property
    def change_indices(self) -> list[int]:
        return [i for i, line in self.lines if line[:1] in ("+", "-")]

    @property
    def last_index(self) -> int:
        return self.lines[-1][0] if self.lines else self.header_index


@dataclass
class FilePatch:
    header: list[str]
    hunks: list[Hunk]
    binary: bool = False

    @property
    def can_stage_lines(self) -> bool:
        return bool(self.hunks) and not self.binary

    def hunk_at(self, line_index: int) -> Hunk | None:
        for hunk in self.hunks:
            if hunk.header_index <= line_index <= hunk.last_index:
                return hunk
        return None

    def changes_in(self, start: int, end: int) -> set[int]:
        """Indices of +/- lines between two diff-text line indices (inclusive)."""
        lo, hi = min(start, end), max(start, end)
        selected: set[int] = set()
        for hunk in self.hunks:
            if lo <= hunk.header_index <= hi:
                # Selecting a hunk's @@ header means "the whole hunk".
                selected.update(hunk.change_indices)
            else:
                selected.update(i for i in hunk.change_indices if lo <= i <= hi)
        return selected


def parse_file_patch(text: str) -> FilePatch:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    header: list[str] = []
    hunks: list[Hunk] = []
    binary = False
    for index, line in enumerate(lines):
        match = _HUNK_HEADER.match(line)
        if match:
            hunks.append(Hunk(index, int(match[1]), int(match[3]), match[5]))
        elif hunks:
            hunks[-1].lines.append((index, line))
        else:
            header.append(line)
            if line.startswith(("Binary files", "GIT binary patch")):
                binary = True
    return FilePatch(header, hunks, binary)


@dataclass
class _Line:
    index: int
    tag: str
    text: str
    no_eol: bool = False  # followed by "\ No newline at end of file"


def _blocks(hunk: Hunk) -> list[list[_Line]]:
    """Split a hunk into runs: each context line alone, each -/+ change block together."""
    blocks: list[list[_Line]] = []
    for index, raw in hunk.lines:
        tag = raw[:1] or " "
        if tag == "\\":
            if blocks:
                blocks[-1][-1].no_eol = True
            continue
        line = _Line(index, tag, raw[1:])
        if tag != " " and blocks and blocks[-1][0].tag != " ":
            blocks[-1].append(line)
        else:
            blocks.append([line])
    return blocks


def build_partial_patch(patch: FilePatch, selected: Iterable[int], reverse: bool = False) -> str | None:
    """Patch with only the `selected` +/- lines, or None if nothing is selected."""
    chosen = set(selected)
    out: list[str] = []
    offset = 0  # running (new - old) size difference of the hunks emitted so far
    # The side git apply matches against: old lines going forward, new lines in reverse.
    anchor, inserted = ("+", "-") if reverse else ("-", "+")

    for hunk in patch.hunks:
        if not chosen.intersection(hunk.change_indices):
            continue
        body: list[str] = []
        old_count = new_count = 0

        # Only called within this iteration, so it always sees the current `body`.
        def emit(tag: str, line: _Line) -> None:
            nonlocal old_count, new_count
            body.append(tag + line.text)  # noqa: B023
            if line.no_eol:
                body.append("\\ No newline at end of file")  # noqa: B023
            if tag != "+":
                old_count += 1
            if tag != "-":
                new_count += 1

        for block in _blocks(hunk):
            if block[0].tag == " ":
                emit(" ", block[0])
                continue
            anchors = [ln for ln in block if ln.tag == anchor]
            picked = [ln for ln in block if ln.tag == inserted and ln.index in chosen]
            last_picked_anchor = max((n for n, ln in enumerate(anchors) if ln.index in chosen), default=None)
            # Unpicked anchor-side lines stay as context; picked inserted-side lines
            # go right after the last picked anchor line, so a picked "-old/+new"
            # pair replaces in place instead of drifting to the end of the block.
            for n, ln in enumerate(anchors):
                emit(anchor if ln.index in chosen else " ", ln)
                if n == last_picked_anchor:
                    for p in picked:
                        emit(inserted, p)
            if last_picked_anchor is None:
                for p in picked:
                    emit(inserted, p)

        # git apply matches the old side going forward and the new side in
        # reverse; that side is unchanged from the original hunk, so only the
        # other side's start needs shifting by the hunks already emitted.
        if reverse:
            new_start = hunk.new_start
            old_start = new_start - offset
        else:
            old_start = hunk.old_start
            new_start = old_start + offset
        out.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{hunk.section}")
        out.extend(body)
        offset += new_count - old_count

    if not out:
        return None
    return "\n".join(patch.header + out) + "\n"
