import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from gitops import __version__
from gitops.app import main

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release_info", ROOT / "scripts" / "release_info.py")
release_info = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_info)


def _project(
    tmp_path, version="1.2.0", changelog="## 1.2.0 - 2026-01-01\n\n### Added\n- A thing.\n\n## 1.1.0\n\n- Old.\n"
):
    (tmp_path / "gitops").mkdir()
    (tmp_path / "gitops" / "__init__.py").write_text(f'"""Doc."""\n\n__version__ = "{version}"\n')
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n{changelog}")
    return tmp_path


def test_notes_are_the_versions_section_only(tmp_path):
    version, notes = release_info.release_notes("v1.2.0", _project(tmp_path))
    assert version == "1.2.0"
    assert notes.startswith("### Added\n- A thing.")
    assert "Old." not in notes and "aren't signed" in notes


def test_mismatched_tag_fails(tmp_path):
    with pytest.raises(SystemExit, match="expected v1.2.0"):
        release_info.release_notes("v1.2.1", _project(tmp_path))


def test_missing_changelog_section_fails(tmp_path):
    with pytest.raises(SystemExit, match="no section for 1.2.0"):
        release_info.release_notes("v1.2.0", _project(tmp_path, changelog="## 1.1.0\n\n- Old.\n"))


def test_similar_version_isnt_matched(tmp_path):
    with pytest.raises(SystemExit, match="no section"):
        release_info.release_notes("v1.2.0", _project(tmp_path, changelog="## 1.2.0.1\n\n- Other.\n"))


def test_current_version_has_notes():
    """The repo itself: the version in the code always has a CHANGELOG entry."""
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_info.py"), f"v{__version__}"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == __version__


def test_version_flag_prints_and_exits(capsys):
    assert main(["gitops", "--version"]) == 0
    assert capsys.readouterr().out.strip() == f"GitOps {__version__}"
