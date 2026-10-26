import os
import subprocess

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402

from gitops.core import ChangeKind  # noqa: E402
from gitops.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def window(qtbot, qapp, tmp_path, repo):
    qapp.setOrganizationName("gitops-tests")
    qapp.setApplicationName("gitops-tests")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))

    repo.write("login.py", "def basic_auth(user):\n    pass\n")
    repo.commit("add login")
    repo.git("mv", "login.py", "auth.py")
    repo.commit("rename")
    repo.write("auth.py", "def oauth(user):\n    pass\n")
    repo.commit("switch to oauth")

    w = MainWindow()
    qtbot.addWidget(w)
    w.open_repo(str(repo.path))
    qtbot.waitUntil(lambda: w.history.model.rowCount() == 3, timeout=5000)
    return w


def test_history_graph_and_sidebar(window):
    assert window.history.model.item(0, 1).text() == "switch to oauth"
    assert "HEAD -> main" in window.history.model.item(0, 1).data(0x0100)
    assert window.history.model.item(0, 0).data(0x0100).column == 0
    assert "main" in window.sidebar.topLevelItem(0).child(0).text(0)
    assert "+def oauth" in window.history.diff.toPlainText()


def test_file_history_view(window, qtbot):
    window.file_history.show_file("auth.py")
    qtbot.waitUntil(lambda: window.file_history.model.rowCount() == 3, timeout=5000)
    kinds = [window.file_history.model.item(r, 0).text() for r in range(3)]
    assert kinds == [ChangeKind.MODIFIED.value, ChangeKind.RENAMED.value, ChangeKind.CREATED.value]


def test_content_search_view(window, qtbot):
    view = window.content_search
    view.query.setText("basic_auth")
    view.start()
    qtbot.waitUntil(lambda: view.search_btn.isEnabled(), timeout=5000)
    kinds = [view.model.item(r, 0).text() for r in range(view.model.rowCount())]
    assert kinds == [ChangeKind.REMOVED.value, ChangeKind.ADDED.value]
    assert "not present" in view.summary.text()
    assert "basic_auth" in view.diff.toPlainText()


def select_diff_line(diff_view, needle):
    doc = diff_view.document()
    block = doc.begin()
    while block.isValid():
        if block.text() == needle:
            cursor = QTextCursor(block)
            diff_view.setTextCursor(cursor)
            return
        block = block.next()
    raise AssertionError(f"{needle!r} not in diff")


def test_stage_selected_line_and_commit(window, repo, qtbot):
    repo.write("auth.py", "def oauth(user):\n    return token\nEXTRA = 1\n")
    window._refresh_status()
    changes = window.changes
    assert changes.unstaged.count() == 1
    assert window.tabs.tabText(0) == "Changes (1)"

    changes.unstaged.setCurrentRow(0)
    select_diff_line(changes.diff, "+EXTRA = 1")
    changes.apply_lines()

    assert changes.staged.count() == 1 and changes.unstaged.count() == 1
    assert repo.git("show", ":auth.py") == "def oauth(user):\n    pass\nEXTRA = 1\n"

    changes.message.setPlainText("Add EXTRA constant")
    changes.commit()
    qtbot.waitUntil(lambda: window.history.model.rowCount() == 4, timeout=5000)
    assert window.history.model.item(0, 1).text() == "Add EXTRA constant"
    assert changes.staged.count() == 0 and changes.unstaged.count() == 1


def test_create_branch_shows_in_sidebar(window, qtbot):
    window.backend.create_branch("feature/x")
    window.refresh()
    qtbot.waitUntil(lambda: window.sidebar.topLevelItem(0).childCount() == 2, timeout=5000)
    assert "feature/x" in window.branch_label.text()


def test_push_through_network_runner(window, tmp_path, qtbot):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    window.backend.run("remote", "add", "origin", str(bare))

    window.push_act.trigger()
    qtbot.waitUntil(lambda: window.network_task is None and window.push_act.isEnabled(), timeout=10000)
    assert "↑0 ↓0" in window.branch_label.text()
