"""Check a release tag against the code and print its CHANGELOG section.

Usage: python scripts/release_info.py v0.3.4 [--notes-out notes.md]

Fails when the tag isn't v<version> for the version in gitops/__init__.py,
or when CHANGELOG.md has no section for that version, so a mistyped tag
never publishes a release. Prints the version on success.
"""

from __future__ import annotations

import argparse
import re
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


def release_notes(tag: str, root: Path = ROOT) -> tuple[str, str]:
    version = code_version(root)
    if tag != f"v{version}":
        raise SystemExit(f"Tag {tag} doesn't match the code's version {version}; expected v{version}")
    return version, changelog_section(version, root) + "\n" + UNSIGNED_NOTE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag")
    parser.add_argument("--notes-out", type=Path, help="write the release notes here")
    args = parser.parse_args(argv)
    version, notes = release_notes(args.tag)
    if args.notes_out:
        args.notes_out.write_text(notes, encoding="utf-8")
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
