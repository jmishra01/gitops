"""The File inspector: versions, timeline, A/B compare, restore, blame and navigation."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402

from gitops.core import GitBackend  # noqa: E402
from gitops.core.file_history import file_history  # noqa: E402
from gitops.ui.main_window import MainWindow  # noqa: E402
from gitops.ui.widgets import file_inspector  # noqa: E402
from gitops.ui.widgets.file_inspector import build_versions, version_bytes  # noqa: E402


@pytest.fixture
def window(qtbot, qapp, tmp_path, repo):
    qapp.setOrganizationName("gitops-inspector-tests")
    qapp.setApplicationName("gitops-inspector-tests")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    repo.write("src/login.py", "v1\n")
    repo.commit("Create login", author="Lena Chen")
    repo.write("src/login.py", "v1\nv2\n")
    repo.commit("Second version", author="Diego Ruiz")
    repo.git("mv", "src/login.py", "src/auth.py")
    repo.commit("Rename to auth", author="Lena Chen")
    repo.write("src/auth.py", "v1\nv2\nv3\n")
    repo.commit("Third version", author="Priya Shah")
    w = MainWindow()
    qtbot.addWidget(w)
    w.open_repo(str(repo.path))
    return w


def open_file(window, qtbot, path="src/auth.py"):
    window._open_file_history(path)
    qtbot.waitUntil(lambda: window.inspector.model.rowCount() >= 2, timeout=5000)
    return window.inspector


def test_header_versions_and_summary(window, qtbot):
    ins = open_file(window, qtbot)
    assert window.current_page() is ins and window.rail.buttons["files"].isChecked()
    assert ins.title.text() == "auth.py"
    assert "was src/login.py until" in ins.subtitle.text()
    assert "4 versions" in ins.summary.text() and "Lena Chen" in ins.summary.text() and "renamed" in ins.summary.text()
    assert [v.delta for v in ins.versions] == ["", "+1", "±0", "+1", "+1"]
    assert [m.kind for m in ins.timeline.marks] == ["working", "modified", "renamed", "modified", "created"]


def test_identical_and_compare_rows(window, qtbot):
    ins = open_file(window, qtbot)
    qtbot.waitUntil(lambda: ins.compare_text() == "A and B are identical.", timeout=5000)  # clean working copy
    ins.set_a(4)
    qtbot.waitUntil(lambda: bool(ins.split.rows), timeout=5000)
    assert [(r.kind, r.left_no, r.right_no) for r in ins.split.rows] == [("equal", 1, 1), ("insert", None, 2), ("insert", None, 3)]
    assert ins.split.first_change() == 1


def test_restore_old_version(window, repo, qtbot, monkeypatch):
    ins = open_file(window, qtbot)
    asked = []

    def answer(_parent, _title, text, *rest):
        asked.append(text)
        return file_inspector.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(file_inspector.QMessageBox, "question", answer)
    ins.restore_version(4)  # the very first version, from before the rename
    assert (repo.path / "src/auth.py").read_text() == "v1\n"
    assert "shows up as an uncommitted change" in asked[0]
    qtbot.waitUntil(lambda: window.changes.unstaged_paths() == ["src/auth.py"], timeout=5000)

    # With local edits, the confirmation must say they'll be lost.
    qtbot.waitUntil(lambda: bool(ins.versions) and ins.versions[0].subject == "Uncommitted changes", timeout=5000)
    ins.restore_version(1)
    assert "uncommitted edits to this file will be lost" in asked[1]


def test_blame_tab_and_rail(window, qtbot):
    ins = open_file(window, qtbot)
    window.rail.buttons["blame"].click()
    assert ins.pages.currentWidget() is ins.blame and window.rail.buttons["blame"].isChecked()
    qtbot.waitUntil(lambda: window.blame.model.rowCount() == 3, timeout=5000)
    window.rail.buttons["files"].click()
    assert ins.pages.currentWidget() is ins.history_page


def test_blame_a_version_from_the_versions_menu(window, qtbot):
    ins = open_file(window, qtbot)
    old = ins.versions[3]
    ins.show_blame(old.path, old.entry.commit.sha)
    qtbot.waitUntil(lambda: window.blame.model.rowCount() == 2, timeout=5000)
    assert window.blame.rev_label.text().startswith("At ")


def test_back_and_search_navigation(window, qtbot):
    window.show_page(window.history)
    ins = open_file(window, qtbot)
    ins.search_act.trigger()
    assert window.current_page() is window.content_search
    assert window.content_search.paths.text() == "src/auth.py"
    window._open_file_history("src/auth.py")
    ins.back_btn.click()
    assert window.current_page() is window.content_search


def test_picker_lists_tracked_files(window, qtbot):
    ins = window.inspector
    qtbot.waitUntil(lambda: "src/auth.py" in ins.completer.model().stringList(), timeout=5000)
    ins.picker.setText("src/auth.py")
    ins.picker.returnPressed.emit()
    qtbot.waitUntil(lambda: ins.model.rowCount() == 5, timeout=5000)


def test_versions_for_deleted_and_binary(repo, tmp_path):
    repo.write("img.bin", "\0\1")
    repo.commit("add binary")
    (repo.path / "img.bin").unlink()
    repo.commit("delete binary")
    backend = GitBackend(repo.path)
    entries = file_history(backend, "img.bin")
    versions = build_versions(backend, "img.bin", entries)
    assert versions[0].subject == "Deleted in the working copy"
    assert [v.kind for v in versions] == ["Working copy", "Deleted", "Created"]
    assert version_bytes(backend, versions[1]) == b"\0\1"  # a deletion shows the last content
    assert versions[2].delta == "binary"


def test_rank_files_prefers_name_matches():
    from gitops.ui.widgets.file_browser import rank_files

    files = ["docs/login-notes.md", "src/auth/login.py", "src/auth/session.py", "tests/test_session.py"]
    assert rank_files(files, "login") == ["src/auth/login.py", "docs/login-notes.md"]
    assert rank_files(files, "auth") == ["src/auth/login.py", "src/auth/session.py"]
    assert rank_files(files, "srclog") == ["src/auth/login.py"]
    assert rank_files(files, "zzz") == []
    assert rank_files(files, "") == files


def test_finder_opens_file_on_the_blame_tab(window, qtbot):
    ins = window.inspector
    window._rail_selected("blame")
    assert ins.pages.currentWidget() is ins.empty
    assert not ins.picker.isVisible()
    qtbot.waitUntil(lambda: "src/auth.py" in ins.empty.files, timeout=5000)
    ins.empty.search.setText("auth")
    ins.empty.search.returnPressed.emit()
    assert ins.path == "src/auth.py"
    assert ins.pages.currentWidget() is ins.blame
    qtbot.waitUntil(lambda: len(ins.blame.lines) == 3, timeout=5000)
    assert ins.empty.recent == ["src/auth.py"]
