"""Diff two arbitrary file versions (e.g. across a rename, or vs the working copy)."""

from __future__ import annotations

import difflib


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
