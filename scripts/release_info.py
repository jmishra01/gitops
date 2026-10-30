"""Check a release tag against the code and write its release notes.

Usage: python scripts/release_info.py v0.3.4 [--notes-out notes.md] [--since v0.3.2]

Fails when the tag isn't v<version> for the version in gitops/__init__.py,
or when CHANGELOG.md has no section for that version, so a mistyped tag
never publishes a release. Prints the version on success.

The notes cover every CHANGELOG section since the previous release: the
highest v* tag below this version, or --since. Versions that were never
tagged are folded into the next release that is.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

UNSIGNED_NOTE = """
---

These builds aren't signed yet.
- **macOS** (Apple silicon): unzip and open GitOps.app. When macOS blocks it, choose System Settings › Privacy & Security › Open Anyway, or run `xattr -dr com.apple.quarantine GitOps.app`.
- **Windows:** if SmartScreen warns, choose More info › Run anyway.
- **Linux:** extract, run `GitOps/GitOps`; `gitops.desktop` and `gitops.png` add it to your app menu.
"""


def code_version(root: Path = ROOT) -> str:
    text = (root / "gitops" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("No __version__ in gitops/__init__.py")
    return match.group(1)


def changelog_section(version: str, root: Path = ROOT) -> str:
    """The body of `## <version> ...` in CHANGELOG.md, up to the next `## ` heading."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}(?:[ \t][^\n]*)?\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if not match or not match.group(1).strip():
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return match.group(1).strip()


def parse_version(text: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def changelog_sections(root: Path = ROOT) -> list[tuple[str, str, str]]:
    """(version, heading line, body) for each `## ` section of CHANGELOG.md, newest first."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    pattern = re.compile(r"^(## (\S+)[^\n]*)\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    return [(m.group(2), m.group(1), m.group(3).strip()) for m in pattern.finditer(text)]


def previous_release(version: str, root: Path = ROOT) -> str | None:
    """The highest v* tag below `version`, or None outside a git checkout or before any tag."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "tag", "--list", "v*"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    current = parse_version(version)
    older = [tag for tag in out.split() if (v := parse_version(tag)) and current and v < current]
    return max(older, key=lambda tag: parse_version(tag) or (), default=None)


def release_notes(tag: str, root: Path = ROOT, since: str | None = None) -> tuple[str, str]:
    version = code_version(root)
    if tag != f"v{version}":
        raise SystemExit(f"Tag {tag} doesn't match the code's version {version}; expected v{version}")
    body = changelog_section(version, root)
    since = since or previous_release(version, root)
    low, high = parse_version(since) if since else None, parse_version(version)
    if low and high:
        sections = [s for s in changelog_sections(root) if (v := parse_version(s[0])) and low < v <= high]
        if len(sections) > 1:
            body = f"Everything since {since}, newest first.\n\n" + "\n\n".join(
                f"{heading}\n\n{text}" for _, heading, text in sections
            )
    return version, body + "\n" + UNSIGNED_NOTE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag")
    parser.add_argument("--notes-out", type=Path, help="write the release notes here")
    parser.add_argument("--since", help="the previous release's tag (default: the highest v* tag below this one)")
    args = parser.parse_args(argv)
    version, notes = release_notes(args.tag, since=args.since)
    if args.notes_out:
        args.notes_out.write_text(notes, encoding="utf-8")
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
