"""Diff two arbitrary file versions (e.g. across a rename, or vs the working copy)."""

from __future__ import annotations

import difflib
from dataclasses import dataclass


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def unified_diff(
    old: bytes | None,
    new: bytes | None,
    old_label: str,
    new_label: str,
    context: int = 3,
) -> str:
    """Unified diff text; `None` means the file doesn't exist on that side."""
    if old is not None and new is not None and old == new:
        return f"{old_label} and {new_label} are identical"
    if (old and is_binary(old)) or (new and is_binary(new)):
        return f"Binary files {old_label} and {new_label} differ"
    a = (old or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
    b = (new or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
    lines = difflib.unified_diff(
        a,
        b,
        fromfile=old_label if old is not None else "/dev/null",
        tofile=new_label if new is not None else "/dev/null",
        n=context,
    )
    out = []
    for line in lines:
        out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)


@dataclass(frozen=True)
class SideBySideRow:
    """One row of a two-column compare. A side with no line here (number None) is drawn as filler."""

    kind: str  # "equal", "replace", "delete" (only on the left), "insert" (only on the right)
    left_no: int | None
    left: str
    right_no: int | None
    right: str


def align(old_lines: list[str], new_lines: list[str]) -> list[SideBySideRow]:
    """Pair up two versions line by line, the way a side-by-side diff shows them."""
    rows: list[SideBySideRow] = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append(SideBySideRow("equal", i1 + k + 1, old_lines[i1 + k], j1 + k + 1, new_lines[j1 + k]))
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append(SideBySideRow("delete", k + 1, old_lines[k], None, ""))
        elif tag == "insert":
            for k in range(j1, j2):
                rows.append(SideBySideRow("insert", None, "", k + 1, new_lines[k]))
        else:  # replace: pair lines up, padding the shorter side
            for k in range(max(i2 - i1, j2 - j1)):
                left = i1 + k < i2
                right = j1 + k < j2
                rows.append(
                    SideBySideRow(
                        "replace",
                        i1 + k + 1 if left else None,
                        old_lines[i1 + k] if left else "",
                        j1 + k + 1 if right else None,
                        new_lines[j1 + k] if right else "",
                    )
                )
    return rows


def lines_of(data: bytes | None) -> list[str]:
    """Text lines for display (lossless bytes would need surrogates, so legacy bytes show as Latin-1)."""
    if not data:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return text.replace("\r\n", "\n").split("\n")[: -1 if text.endswith("\n") else None]
