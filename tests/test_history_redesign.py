"""The redesigned History screen: commit list, details panel and its actions."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402

from gitops.ui import theme  # noqa: E402
from gitops.ui.main_window import MainWindow  # noqa: E402
from gitops.ui.widgets.commit_details import FILE_ROLE, change_bars  # noqa: E402
from gitops.ui.widgets.common import AVATAR_COLORS, avatar_color, initials  # noqa: E402
from gitops.ui.widgets.history_view import SubjectDelegate  # noqa: E402


@pytest.fixture
def window(qtbot, qapp, tmp_path, repo):
    qapp.setOrganizationName("gitops-hist-tests")
    qapp.setApplicationName("gitops-hist-tests")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    repo.write("src/login.py", "def login(user):\n    check(user)\n")
    repo.write("README.md", "readme\n")
    repo.commit("Base", author="Lena Chen")
    repo.write("src/login.py", "def login(user):\n    token = oauth(user)\n    return token\n")
    repo.write("src/oauth.py", "def oauth(user):\n    return 'token'\n")
    repo.commit("Switch login to OAuth\n\nReplaces password checks with tokens.", author="Priya Shah")
    repo.git("tag", "v1.0")
    w = MainWindow()
    qtbot.addWidget(w)
    w.open_repo(str(repo.path))
    w.show_page(w.history)
    qtbot.waitUntil(lambda: w.history.model.rowCount() == 2, timeout=5000)
    qtbot.waitUntil(lambda: w.history.details.file_model.rowCount() == 2, timeout=5000)
    return w


def test_details_show_message_files_and_first_diff(window, qtbot):
    d = window.history.details
    assert d.subject.text() == "Switch login to OAuth"
    assert d.body.text() == "Replaces password checks with tokens."
    assert "Priya Shah" in d.byline.text() and "committed" in d.byline.text()
    assert d.sha_button.text().startswith(window.history.commits[0].short_sha)
    assert "2 FILES CHANGED" in d.files_label.text()
    files = [d.file_model.item(r).data(FILE_ROLE) for r in range(2)]
    assert [f.path for f in files] == ["src/login.py", "src/oauth.py"]
    assert "+    token = oauth(user)" in d.diff_text()

    assert d.select_file("src/oauth.py")
    assert "+def oauth(user):" in d.diff_text()


def test_selecting_another_commit_updates_details(window, qtbot):
    window.history.table.selectRow(1)
    d = window.history.details
    qtbot.waitUntil(lambda: d.subject.text() == "Base" and d.file_model.rowCount() == 2, timeout=5000)
    assert not d.body.isVisibleTo(d)  # no body text for this commit
    assert "Lena Chen" in d.byline.text()


def test_actions_new_branch_and_detached_checkout(window, monkeypatch):
    from gitops.ui import main_window

    base = window.history.commits[1]
    monkeypatch.setattr(main_window.QInputDialog, "getText", lambda *a, **k: ("from-base", True))
    window.history.details.commit = base
    window.history.details._emit("new_branch")
    assert window.backend.current_branch() == "from-base"
    assert window.backend.run("rev-parse", "HEAD").strip() == base.sha

    head = window.backend.log(rev="main")[0]
    window.history.details.commit = head
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *a, **k: main_window.QMessageBox.StandardButton.Yes)
    window.history.details._emit("checkout")
    assert window.backend.current_branch() is None
    assert window.backend.run("rev-parse", "HEAD").strip() == head.sha


def test_scope_toggle_is_exclusive(window, qtbot):
    h = window.history
    assert h.current_only.isChecked() and not h.all_refs.isChecked()
    h.all_refs.click()
    assert h.all_refs.isChecked() and not h.current_only.isChecked()


def test_helpers():
    assert initials("Priya Shah") == "PS" and initials("dev") == "D" and initials("") == "?"
    assert avatar_color("p@x") == avatar_color("p@x") and avatar_color("p@x") in AVATAR_COLORS
    assert change_bars(3, 2) == ["add", "add", "add", "del", "del"]
    assert change_bars(None, None) == ["neutral"] * 5
    assert change_bars(0, 0) == ["neutral"] * 5
    assert change_bars(48, 0) == ["add"] * 5


def _luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("palette", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_badges_and_selected_rows_are_readable(palette):
    """Regression for the unreadable selected-row badge: every pairing must pass WCAG AA (4.5:1)."""
    delegate = SubjectDelegate()
    delegate.remotes = {"origin"}
    for ref in ("HEAD -> main", "main", "origin/main", "tag: v1.0", "HEAD"):
        _label, bg, fg = delegate._style(ref)
        assert contrast(getattr(palette, bg), getattr(palette, fg)) >= 4.5, ref
    assert contrast(palette.accent_soft, palette.text) >= 4.5  # subject on the selected row
    assert contrast(palette.surface, palette.text_secondary) >= 4.5  # author and time
    for color in AVATAR_COLORS:
        assert contrast(color, "#FFFFFF") >= 4.5, color
