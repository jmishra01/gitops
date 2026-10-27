"""Keep file bytes intact no matter what encoding a file uses.

Git stores bytes, not text. Decoding with errors="replace" silently turns
every byte that isn't valid UTF-8 into U+FFFD, and writing that back
corrupts the file. Instead:

* Text that only travels between git commands (patches for line staging)
  is decoded with "surrogateescape", which round-trips any byte exactly.
* Text shown in Qt goes through `display_text`, since Qt can't hold the
  lone surrogates that escaping produces.
* Text the user edits (the conflict editor) uses `pick_encoding`: UTF-8 when
  every version is valid UTF-8, otherwise Latin-1, which maps each byte to
  exactly one character and back.
"""

from __future__ import annotations

LOSSLESS = "surrogateescape"
FALLBACK = "latin-1"


def decode_lossless(data: bytes) -> str:
    return data.decode("utf-8", LOSSLESS)


def encode_lossless(text: str) -> bytes:
    return text.encode("utf-8", LOSSLESS)


def display_text(text: str) -> str:
    """Make losslessly-decoded text safe for Qt, keeping line breaks where they are.

    Lines that aren't valid UTF-8 are shown as Latin-1, so a legacy "café"
    reads as café rather than as a replacement character.
    """
    lines = text.split("\n")
    out = []
    for line in lines:
        raw = line.encode("utf-8", LOSSLESS)
        try:
            out.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            out.append(raw.decode(FALLBACK))
    return "\n".join(out)


def is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def pick_encoding(*versions: bytes | None) -> str:
    """UTF-8 if every version decodes as UTF-8, otherwise Latin-1 (which never fails)."""
    return "utf-8" if all(is_utf8(v) for v in versions if v is not None) else FALLBACK
