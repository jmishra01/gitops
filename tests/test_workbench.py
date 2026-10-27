"""The redesigned Changes workbench, top bar and nav rail."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402

from gitops.ui.main_window import MainWindow  # noqa: E402
from gitops.ui.widgets.file_list import ROW_ROLE, FileRow, FileRowDelegate  # noqa: E402
from gitops.ui.widgets.top_bar import SyncState  # noqa: E402

BASE = "".join(f"line {n}\n" for n in range(1, 21))


@pytest.fixture
def window(qtbot, qapp, tmp_path, repo):
    qapp.setOrganizationName("gitops-wb-tests")
    qapp.setApplicationName("gitops-wb-tests")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    repo.write("src/app.py", BASE)
    repo.write("README.md", "readme\n")
    repo.commit("base")
    w = MainWindow()
    qtbot.addWidget(w)
    w.open_repo(str(repo.path))
    return w


def click(widget, point: QPoint, button=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier):
    pos = QPointF(point)
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        event = QMouseEvent(kind, pos, widget.mapToGlobal(pos), button, button, modifiers)
        if kind == QEvent.Type.MouseButtonPress:
            widget.mousePressEvent(event)
        else:
            widget.mouseReleaseEvent(event)


def checkbox_center(files) -> QPoint:
    """Centre of the first file row's checkbox (row 0 is the section header)."""
    index = next(
        files.model_.index(r, 0)
        for r in range(files.model_.rowCount())
        if isinstance(files.model_.item(r).data(ROW_ROLE), FileRow)
    )
    return FileRowDelegate.checkbox_rect(files.visualRect(index)).center()


def edit_two_hunks(repo):
    lines = BASE.splitlines()
    lines[1] = "TWO"
    lines[16] = "SEVENTEEN"
    repo.write("src/app.py", "\n".join(lines) + "\n")


def test_rows_sections_counts_and_notes(window, repo):
    edit_two_hunks(repo)
    repo.write("notes/new.txt", "a\nb\n")
    window.backend.stage("README.md")  # unchanged file: stays out of both lists
    window._refresh_status()
    rows = {(r.path, r.staged): r for r in window.changes.files.rows()}
    assert set(rows) == {("src/app.py", False), ("notes/new.txt", False)}
    app = rows[("src/app.py", False)]
    assert (app.letter, app.added, app.removed, app.folder, app.name) == ("M", 2, 2, "src/", "app.py")
    assert rows[("notes/new.txt", False)].letter == "?"
    assert window.changes.composer.button.text() == "Commit all 2 changes to main"


def test_checkbox_click_stages_and_unstages(window, repo):
    edit_two_hunks(repo)
    window._refresh_status()
    files = window.changes.files
    files.resize(340, 400)
    click(files, checkbox_center(files))
    assert window.changes.staged_paths() == ["src/app.py"]
    assert window.changes.composer.button.text() == "Commit 1 file to main"

    click(files, checkbox_center(files))
    assert window.changes.staged_paths() == []


def test_partly_staged_after_stage_hunk_button(window, repo):
    edit_two_hunks(repo)
    window._refresh_status()
    changes = window.changes
    changes.select_file("src/app.py", staged=False)
    assert len(changes.hunks.cards) == 2
    assert changes.hunks.cards[1].action.text() == "Stage hunk"
    changes.hunks.cards[1].action.click()

    assert changes.staged_paths() == ["src/app.py"]
    partly = [r for r in changes.files.rows() if not r.staged]
    assert partly[0].note == "partly staged"
    assert "SEVENTEEN" in window.backend.run("show", ":src/app.py")
    assert "TWO" not in window.backend.run("show", ":src/app.py")

    changes.select_file("src/app.py", staged=True)
    assert changes.hunks.cards[0].action.text() == "Unstage hunk"
    assert changes.file_btn.text() == "Unstage file" and changes.discard_btn.isHidden()


def test_drag_and_shift_click_select_lines(window, repo):
    lines = BASE.splitlines()
    lines[4:7] = ["FIVE", "SIX", "SEVEN"]
    repo.write("src/app.py", "\n".join(lines) + "\n")
    window._refresh_status()
    changes = window.changes
    changes.select_file("src/app.py")
    card = changes.hunks.cards[0]
    rows = card.lines.lines
    first = next(i for i, ln in enumerate(rows) if ln.is_change)
    last = max(i for i, ln in enumerate(rows) if ln.is_change)
    y = lambda row: int(row * 22 + 11)  # noqa: E731
    click(card.lines, QPoint(10, y(first)))
    click(card.lines, QPoint(10, y(last)), modifiers=Qt.KeyboardModifier.ShiftModifier)
    assert len(changes.hunks.selected) == 6  # three removed + three added
    click(card.lines, QPoint(10, y(first)))  # untick one
    assert len(changes.hunks.selected) == 5
    assert "5 lines selected" in changes.selection_label.text()
    changes.hunks.clear_selection()
    assert changes.selection_bar.isHidden()


def test_discard_lines_only_touches_selected(window, repo, monkeypatch):
    from gitops.ui.widgets import changes_view

    edit_two_hunks(repo)
    window._refresh_status()
    changes = window.changes
    changes.select_file("src/app.py")
    for raw in ("-line 2", "+TWO"):
        changes.hunks.set_line(changes.hunks.find_line(raw), True)
    monkeypatch.setattr(
        changes_view.QMessageBox, "warning", lambda *a, **k: changes_view.QMessageBox.StandardButton.Discard
    )
    changes.discard_selected_lines()
    text = (repo.path / "src/app.py").read_text()
    assert "TWO" not in text and "line 2\n" in text and "SEVENTEEN" in text


def test_untracked_and_empty_states(window, repo):
    changes = window.changes
    assert not changes.empty_files.isHidden() and changes.files.isHidden()
    repo.write("new.txt", "hello\n")
    window._refresh_status()
    changes.select_file("new.txt")
    assert changes.stack.currentWidget() is changes.text_view
    assert "+hello" in changes.current_text()
    assert changes.discard_btn.text() == "Delete file…"


@pytest.mark.parametrize(
    "has_remote, ab, branch, action, label",
    [
        (False, None, "main", "none", "No remote"),
        (True, None, "main", "publish", "Publish branch"),
        (True, (0, 0), "main", "fetch", "Up to date"),
        (True, (0, 3), "main", "pull", "Pull ↓3"),
        (True, (2, 0), "main", "push", "Push ↑2"),
        (True, (1, 2), "main", "sync", "Sync ↑1 ↓2"),
    ],
)
def test_sync_state(has_remote, ab, branch, action, label):
    state = SyncState.from_repo(has_remote, ab, branch)
    assert (state.action, state.label) == (action, label)


def test_quiet_sync_button_when_nothing_to_do(window):
    window._refresh_status()
    assert window.top_bar.sync_button.text() == "No remote"
    assert window.top_bar.sync_button.property("variant") == "pill"


def test_rail_switches_pages(window):
    assert window.current_page() is window.changes
    window.rail.buttons["history"].click()
    assert window.current_page() is window.history
    window.rail.buttons["branches"].click()
    assert window.current_page() is window.branches
    window.rail.buttons["stashes"].click()
    assert window.current_page() is window.stashes
    window.show_page(window.changes)
    assert window.rail.buttons["changes"].isChecked()


def test_top_bar_menus(window, repo):
    window.backend.create_branch("feature/x", checkout=False)
    window._fill_branch_menu()
    labels = [a.text() for a in window.top_bar.branch_menu.actions()]
    assert "main" in labels and "feature/x" in labels
    window._fill_repo_menu()
    assert any(a.text() == "repo" and a.isChecked() for a in window.top_bar.repo_menu.actions())
    assert window.top_bar.repo_button.text().startswith("repo")
