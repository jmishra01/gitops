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


def test_history_graph_and_sidebar(window, qtbot):
    assert window.history.model.item(0, 1).text() == "switch to oauth"
    assert "HEAD -> main" in window.history.model.item(0, 1).data(0x0100)
    assert window.history.model.item(0, 0).data(0x0100).column == 0
    assert "main" in window.sidebar.topLevelItem(0).child(0).text(0)
    qtbot.waitUntil(lambda: "+def oauth" in window.history.diff.toPlainText(), timeout=5000)


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
    qtbot.waitUntil(lambda: "basic_auth" in view.diff.toPlainText(), timeout=5000)


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


# ---------------------------------------------------------------- v0.2 flows


def test_merge_conflict_resolved_in_editor(window, repo, qtbot):
    from gitops.core.conflicts import Choice
    from gitops.ui.dialogs.conflict_editor import ConflictEditor

    repo.git("switch", "-q", "-c", "feature")
    repo.write("auth.py", "def oauth(user):\n    return 'feature'\n")
    repo.commit("feature change")
    repo.git("switch", "-q", "main")
    repo.write("auth.py", "def oauth(user):\n    return 'main'\n")
    repo.commit("main change")

    assert window.backend.merge("feature") == ["auth.py"]
    window._refresh_status()
    changes = window.changes
    assert changes.banner.isVisibleTo(window) and "Merging" in changes.banner_label.text()
    assert changes.conflicts == ["auth.py"]
    assert changes.unstaged.item(0).text().startswith("U")
    assert "Merge branch 'feature'" in changes.message.toPlainText()

    editor = ConflictEditor(window.backend, "auth.py", window)
    assert "Conflict 1 of 1" in editor.counter.text()
    editor._resolve_current(Choice.THEIRS)
    assert "No conflicts left" in editor.counter.text()
    editor._mark_resolved()
    assert window.backend.conflicted_paths() == []

    changes.refresh()
    assert "All conflicts resolved" in changes.banner_label.text()
    changes.commit()
    qtbot.waitUntil(lambda: window.history.commits and window.history.commits[0].is_merge, timeout=5000)
    assert not changes.banner.isVisibleTo(window)
    assert (repo.path / "auth.py").read_text() == "def oauth(user):\n    return 'feature'\n"


def test_stash_shows_in_sidebar_and_applies(window, repo, qtbot):
    repo.write("auth.py", "stashed change\n")
    window.backend.stash_save("my wip")
    window.refresh()

    def stash_root():
        for i in range(window.sidebar.topLevelItemCount()):
            if window.sidebar.topLevelItem(i).text(0).startswith("Stashes"):
                return window.sidebar.topLevelItem(i)

    qtbot.waitUntil(lambda: stash_root() is not None and stash_root().childCount() == 1, timeout=5000)
    stash = stash_root().child(0).data(0, 0x0102)
    assert "my wip" in stash.message
    window._apply_stash(stash, pop=True)
    assert (repo.path / "auth.py").read_text() == "stashed change\n"
    assert window.backend.stashes() == []


def test_file_history_compare_and_working_copy(window, repo, qtbot):
    view = window.file_history
    view.show_file("auth.py")
    qtbot.waitUntil(lambda: view.model.rowCount() == 3, timeout=5000)
    assert view.timeline.isEnabled() and view.timeline.value() == 2

    view.timeline.setValue(0)  # oldest version
    assert view.table.currentIndex().row() == 2
    repo.write("auth.py", "working copy\n")
    view._diff_vs_working()
    qtbot.waitUntil(lambda: "+working copy" in view.diff.toPlainText(), timeout=5000)
    assert "-def basic_auth(user):" in view.diff.toPlainText()

    sel = view.table.selectionModel()
    view.table.selectRow(0)
    sel.select(view.model.index(2, 0), sel.SelectionFlag.Select | sel.SelectionFlag.Rows)
    assert view.compare_btn.isEnabled()
    view._compare_selected()
    qtbot.waitUntil(lambda: "+def oauth(user):" in view.diff.toPlainText(), timeout=5000)
    assert "login.py @" in view.diff.toPlainText() and "auth.py @" in view.diff.toPlainText()


def test_search_at_commit_and_dates(window, qtbot):
    view = window.content_search
    view.query.setText("basic_auth")
    view.since.setText("2000-01-01")
    view.start()
    qtbot.waitUntil(lambda: view.search_btn.isEnabled() and view.model.rowCount() == 2, timeout=5000)
    view.table.selectRow(1)  # the commit that added it
    view._show_at_commit()
    qtbot.waitUntil(lambda: "matches in 1 file(s)" in view.diff.toPlainText(), timeout=5000)

    view.until.setText("2000-01-02")
    view.start()
    qtbot.waitUntil(lambda: view.search_btn.isEnabled(), timeout=5000)
    assert view.model.rowCount() == 0


def test_history_pages_in_more_commits(window, repo, qtbot, monkeypatch):
    for n in range(5):
        repo.write("auth.py", f"v{n}\n")
        repo.commit(f"commit {n}")
    monkeypatch.setattr(window.history, "PAGE_SIZE", 3)
    window.refresh()
    qtbot.waitUntil(lambda: window.history.commits[0].subject == "commit 4", timeout=5000)
    assert window.history.model.rowCount() == 3
    assert window.history.has_more and "3+ commits" in window.history.count_label.text()
    window.history._maybe_load_more(window.history.table.verticalScrollBar().maximum())
    qtbot.waitUntil(lambda: window.history.model.rowCount() == 6, timeout=5000)
    window.history._maybe_load_more(window.history.table.verticalScrollBar().maximum())
    qtbot.waitUntil(lambda: window.history.model.rowCount() == 8, timeout=5000)
    assert not window.history.has_more


def test_commit_subject_hint(window):
    changes = window.changes
    changes.message.setPlainText("x" * 80 + "\nnot blank")
    assert "80/72" in changes.message_hint.text()
    assert "subject is long" in changes.message_hint.text() and "line 2 blank" in changes.message_hint.text()


def test_history_filter(window, qtbot):
    window.history.filter_edit.setText("rename")
    window.history.filter_requested.emit("rename")
    qtbot.waitUntil(lambda: window.history.filtered and window.history.model.rowCount() == 1, timeout=5000)
    assert "matching" in window.history.count_label.text()
    window.history.filter_edit.clear()  # clearing reloads the full history
    qtbot.waitUntil(lambda: not window.history.filtered and window.history.model.rowCount() == 3, timeout=5000)


def test_blame_tab(window, repo, qtbot):
    repo.write("auth.py", "def oauth(user):\n    pass\nnew line\n")
    window._open_blame("auth.py", None)
    qtbot.waitUntil(lambda: window.blame.model.rowCount() == 3, timeout=5000)
    assert window.blame.model.item(2, 0).text() == "uncommitted"
    assert window.blame.model.item(0, 4).text() == "def oauth(user):"
    window.blame.table.selectRow(0)
    qtbot.waitUntil(lambda: "switch to oauth" in window.blame.diff.toPlainText(), timeout=5000)


def test_cherry_pick_conflict_continue_from_banner(window, repo, qtbot):
    repo.git("switch", "-q", "-c", "side")
    repo.write("auth.py", "side version\n")
    picked = repo.commit("side edit")
    repo.git("switch", "-q", "main")
    repo.write("auth.py", "main version\n")
    repo.commit("main edit")

    assert window.backend.cherry_pick(picked) == ["auth.py"]
    window._refresh_status()
    changes = window.changes
    assert "Cherry-picking" in changes.banner_label.text()
    assert changes.continue_btn.isVisibleTo(window) and not changes.continue_btn.isEnabled()

    window.backend.mark_resolved("auth.py", b"merged version\n")
    changes.refresh()
    assert changes.continue_btn.isEnabled()
    changes.continue_operation()
    assert window.backend.operation_in_progress() is None
    assert not changes.banner.isVisibleTo(window)
    assert window.backend.log()[0].subject == "side edit"


def test_rebase_dialog_validation(window):
    from gitops.core.rebase import rebase_plan
    from gitops.ui.dialogs.rebase_dialog import RebaseDialog

    steps = rebase_plan(window.backend, None)
    dialog = RebaseDialog(steps, "the start", False, window)
    assert dialog.start_btn.isEnabled()
    dialog.list.setCurrentRow(0)
    dialog._set_action("squash")
    assert not dialog.start_btn.isEnabled() and "can't be squashed" in dialog.error.text()
    dialog._set_action("pick")
    dialog.list.setCurrentRow(2)
    dialog._move(-1)
    assert [s.commit.subject for s in dialog.steps()] == ["add login", "switch to oauth", "rename"]


# ---------------------------------------------------------------- polish


def test_command_palette_matching_and_run(window, qtbot):
    from gitops.ui.dialogs.command_palette import CommandPalette, matches

    assert matches("nbr", "Repository › New branch…")
    assert not matches("zzz", "Repository › New branch…")
    labels = [label for label, _ in window._palette_actions()]
    assert "View › Theme › Dark" in labels and "Repository › Undo…" in labels

    palette = CommandPalette(window._palette_actions(), window)
    palette.query.setText("search hist")
    assert palette.list.count() >= 1
    assert "Search history" in palette.list.item(0).text()
    palette._run_current()
    assert window.tabs.currentWidget() is window.content_search


def test_git_console_logs_writes_not_reads(window, repo):
    from gitops.ui.widgets.git_console import is_read_only

    assert is_read_only(["-c", "merge.conflictStyle=diff3", "status"])
    assert is_read_only(["stash", "list"]) and not is_read_only(["stash", "push", "-q"])
    assert not is_read_only(["commit", "-q", "-F", "-"])

    window.console.output.clear()
    window.backend.create_branch("console-test", checkout=False)
    window.backend.status()
    QApplication_process()
    text = window.console.output.toPlainText()
    assert "$ git branch console-test HEAD" in text and "status" not in text


def QApplication_process():
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()


def test_theme_switch_recolors_views(window, qtbot):
    from gitops.ui import theme

    try:
        window._set_theme("dark")
        assert theme.is_dark()
        assert window.history.diff.highlighter.added.background().color().name() == theme.DARK.add_bg
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QApplication

        assert QApplication.palette().color(QPalette.ColorRole.Base).name() == "#0d1117"
        window._set_theme("light")
        assert window.history.diff.highlighter.added.background().color().name() == theme.LIGHT.add_bg
    finally:
        theme.apply("light")


def test_undo_dialog_restores_before_amend(window, repo):
    from gitops.ui.dialogs.undo_dialog import UndoDialog

    before = window.backend.log()[0]
    window.backend.commit("amended subject", amend=True, allow_empty=True)
    dialog = UndoDialog(window.backend, window)
    assert dialog.entries[0].action.startswith("commit (amend)")
    assert dialog.table.currentIndex().row() == 1
    window.backend.restore_to(dialog.entries[1].sha)  # what the Restore button does after confirming
    assert window.backend.log()[0].sha == before.sha


# ---------------------------------------------------------------- v0.3


def test_dashboard_lists_repos_with_state(window, repo, tmp_path, qtbot):
    other = tmp_path / "other"
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    repo.write("dirty.txt", "x\n")

    window._open_dashboard()
    dash = window.dashboard
    dash.set_repos([str(repo.path), str(other), str(tmp_path / "missing")])
    qtbot.waitUntil(lambda: dash.model.rowCount() == 3 and "repositories" in dash.status.text(), timeout=5000)
    states = {dash.model.item(r, 0).text(): dash.model.item(r, 6).text() for r in range(3)}
    assert states == {"missing": "Unavailable", "other": "clean", "repo": "uncommitted changes"}

    dash.table.selectRow([dash.model.item(r, 0).text() for r in range(3)].index("other"))
    dash._open_selected()
    assert window.backend.root.name == "other"
    dash.close()


def test_gitignore_dialog_tests_unsaved_rules(window, repo):
    from gitops.ui.dialogs.gitignore_dialog import GitignoreDialog

    dialog = GitignoreDialog(window.backend, window)
    dialog.editor.setPlainText("*.tmp\n")
    dialog.test_path.setText("cache/x.tmp")
    assert "Ignored by “*.tmp”" in dialog.result.text()
    assert not (repo.path / ".gitignore").exists()  # testing never writes the real file
    dialog._save()
    assert (repo.path / ".gitignore").read_text() == "*.tmp\n"


def test_ignore_untracked_from_changes(window, repo):
    repo.write("logs/run.log", "x\n")
    window._refresh_status()
    window.changes._ignore(["*.log"])
    assert (repo.path / ".gitignore").read_text() == "*.log\n"
    assert [window.changes.unstaged.item(r).data(0x0100) for r in range(window.changes.unstaged.count())] == [".gitignore"]


def test_worktree_section_in_sidebar(window, tmp_path, qtbot):
    window.backend.add_worktree(str(tmp_path / "wt"), "wt-branch", new_branch=True)
    window.refresh()

    def section():
        for i in range(window.sidebar.topLevelItemCount()):
            if window.sidebar.topLevelItem(i).text(0).startswith("Worktrees"):
                return window.sidebar.topLevelItem(i)

    qtbot.waitUntil(lambda: section() is not None, timeout=5000)
    assert section().childCount() == 2
    assert "(this one)" in section().child(0).text(0)
