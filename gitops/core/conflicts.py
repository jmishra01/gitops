"""Parse and resolve git conflict markers in a working-tree file.

Handles both the default "merge" style and "diff3"/"zdiff3" (which add a
||||||| base section). Lines keep their original endings so resolving a
CRLF file doesn't rewrite it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

OURS_MARK = "<<<<<<<"
BASE_MARK = "|||||||"
SPLIT_MARK = "======="
THEIRS_MARK = ">>>>>>>"


class Choice(str, Enum):
    OURS = "ours"
    THEIRS = "theirs"
    BOTH = "both"  # ours then theirs
    BASE = "base"


@dataclass
class ConflictBlock:
    start: int  # line index of the <<<<<<< marker
    end: int  # line index of the >>>>>>> marker
    ours: list[str]
    theirs: list[str]
    base: list[str] | None
    ours_label: str
    theirs_label: str

    def lines_for(self, choice: Choice) -> list[str]:
        if choice is Choice.OURS:
            return self.ours
        if choice is Choice.THEIRS:
            return self.theirs
        if choice is Choice.BASE:
            return self.base or []
        return _join(self.ours, self.theirs)


def _join(first: list[str], second: list[str]) -> list[str]:
    if first and not first[-1].endswith(("\n", "\r")) and second:
        first = first[:-1] + [first[-1] + "\n"]
    return first + second


def _is_marker(line: str, mark: str) -> bool:
    return line.startswith(mark) and (len(line) == len(mark) or line[len(mark)] in " \r\n")


def parse_conflicts(text: str) -> list[ConflictBlock]:
    lines = text.splitlines(keepends=True)
    blocks: list[ConflictBlock] = []
    i = 0
    while i < len(lines):
        if not _is_marker(lines[i], OURS_MARK):
            i += 1
            continue
        start = i
        ours: list[str] = []
        base: list[str] | None = None
        theirs: list[str] = []
        section = ours
        j = i + 1
        while j < len(lines):
            line = lines[j]
            if _is_marker(line, BASE_MARK) and section is ours:
                base = []
                section = base
            elif _is_marker(line, SPLIT_MARK) and section is not theirs:
                section = theirs
            elif _is_marker(line, THEIRS_MARK) and section is theirs:
                break
            else:
                section.append(line)
            j += 1
        if j >= len(lines):
            break  # unterminated block: leave the rest alone
        blocks.append(
            ConflictBlock(
                start=start,
                end=j,
                ours=ours,
                theirs=theirs,
                base=base,
                ours_label=lines[start][len(OURS_MARK):].strip(),
                theirs_label=lines[j][len(THEIRS_MARK):].strip(),
            )
        )
        i = j + 1
    return blocks


def resolve(text: str, choices: dict[int, Choice]) -> str:
    """Replace the conflict blocks numbered in `choices`; others stay as they are."""
    lines = text.splitlines(keepends=True)
    blocks = parse_conflicts(text)
    out: list[str] = []
    cursor = 0
    for n, block in enumerate(blocks):
        if n not in choices:
            continue
        out.extend(lines[cursor:block.start])
        out.extend(block.lines_for(choices[n]))
        cursor = block.end + 1
    out.extend(lines[cursor:])
    return "".join(out)


def resolve_all(text: str, choice: Choice) -> str:
    return resolve(text, {n: choice for n in range(len(parse_conflicts(text)))})


def has_conflict_markers(text: str) -> bool:
    return bool(parse_conflicts(text))
