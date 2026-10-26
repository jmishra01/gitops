import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402

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
    qtbot.waitUntil(lambda: w.history_model.rowCount() == 3, timeout=5000)
    return w


def test_history_and_sidebar(window):
    assert window.history_model.item(0, 1).text() == "switch to oauth"
    assert "main" in window.sidebar.topLevelItem(0).child(0).text(0)
    assert "+def oauth" in window.commit_diff.toPlainText()


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
