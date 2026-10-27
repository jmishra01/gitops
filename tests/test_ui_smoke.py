import os
import subprocess
from pathlib import Path

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
    qtbot.waitUntil(lambda: w.history.model.rowCount() == 3, timeout=5000)
    return w


def test_history_graph_and_branches_page(window, qtbot):
    assert window.history.model.item(0, 1).text() == "switch to oauth"
    assert "HEAD -> main" in window.history.model.item(0, 1).data(0x0100)
    assert window.history.model.item(0, 0).data(0x0100).column == 0
    qtbot.waitUntil(lambda: [r.title for r in window.branches.list.rows()] == ["main"], timeout=5000)
    assert window.branches.list.rows()[0].current
    qtbot.waitUntil(lambda: "+def oauth(user):" in window.history.details.diff_text(), timeout=5000)


def test_file_history_view(window, qtbot):
    window.file_history.show_file("auth.py")
    qtbot.waitUntil(lambda: window.file_history.model.rowCount() == 4, timeout=5000)
    kinds = [v.kind for v in window.file_history.versions]
    assert kinds == ["Working copy", ChangeKind.MODIFIED.value, ChangeKind.RENAMED.value, ChangeKind.CREATED.value]
    assert window.file_history.versions[0].subject == "Same as the last commit"


def test_content_search_view(window, qtbot):
    view = window.content_search
    view.query.setText("basic_auth")
    view.start()
    qtbot.waitUntil(lambda: view.search_btn.isEnabled(), timeout=5000)
    kinds = [view.model.item(r, 0).text() for r in range(view.model.rowCount())]
    assert kinds == [ChangeKind.REMOVED.value, ChangeKind.ADDED.value]
    assert "not present" in view.summary.text()
    qtbot.waitUntil(lambda: "basic_auth" in view.diff.toPlainText(), timeout=5000)


def tick_line(changes, raw):
    """Tick one diff line's checkbox, as a click in the gutter does."""
    index = changes.hunks.find_line(raw)
    assert index is not None, f"{raw!r} not in diff"
    changes.hunks.set_line(index, True)


def test_stage_selected_line_and_commit(window, repo, qtbot):
    repo.write("auth.py", "def oauth(user):\n    return token\nEXTRA = 1\n")
    window._refresh_status()
    changes = window.changes
    assert changes.unstaged_paths() == ["auth.py"]
    assert window.rail.badge_text() == "1"

    assert changes.select_file("auth.py", staged=False)
    tick_line(changes, "+EXTRA = 1")
    assert not changes.selection_bar.isHidden() and "1 line selected" in changes.selection_label.text()
    changes.apply_selected_lines()

    assert changes.staged_paths() == ["auth.py"] and changes.unstaged_paths() == ["auth.py"]
    assert repo.git("show", ":auth.py") == "def oauth(user):\n    pass\nEXTRA = 1\n"
    assert changes.composer.button.text() == "Commit 1 file to main"

    changes.composer.summary.setText("Add EXTRA constant")
    changes.commit()
    qtbot.waitUntil(lambda: window.history.model.rowCount() == 4, timeout=5000)
    assert window.history.model.item(0, 1).text() == "Add EXTRA constant"
    assert changes.staged_paths() == [] and changes.unstaged_paths() == ["auth.py"]


def test_create_branch_shows_on_branches_page(window, qtbot):
    window.backend.create_branch("feature/x")
    window.refresh()
    qtbot.waitUntil(lambda: len([r for r in window.branches.list.rows() if r.kind == "local"]) == 2, timeout=5000)
    qtbot.waitUntil(lambda: "feature/x" in window.top_bar.branch_button.text(), timeout=5000)


def test_push_through_network_runner(window, tmp_path, qtbot):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    window.backend.run("remote", "add", "origin", str(bare))

    window._refresh_status()
    assert window.top_bar.sync_state.action == "publish"  # no upstream yet
    window.top_bar.sync_requested.emit()  # what clicking Sync does
    qtbot.waitUntil(lambda: window.network_task is None and window.push_act.isEnabled(), timeout=10000)
    qtbot.waitUntil(lambda: window.top_bar.sync_button.text() == "Up to date", timeout=5000)


# ---------------------------------------------------------------- v0.2 flows


def test_merge_conflict_resolved_in_editor(window, repo, qtbot):
    from gitops.core.conflicts import Choice
    from gitops.ui.dialogs.conflict_editor import ConflictResolver

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
    assert [r.letter for r in changes.files.rows()] == ["U"]
    assert "Merge branch 'feature'" in changes.composer.summary.text()
    assert changes.composer.button.text() == "Commit merge"

    resolver = ConflictResolver(window.backend, "auth.py", window)
    assert "Conflict 1 of 1" in resolver.counter.text()
    resolver.decide(0, Choice.THEIRS)
    assert "all decided" in resolver.counter.text()
    resolver.mark_resolved()
    assert window.backend.conflicted_paths() == []
    resolver._finish()
    assert resolver.outcome == "commit"

    changes.refresh()
    assert "All conflicts resolved" in changes.banner_label.text()
    changes.commit()
    qtbot.waitUntil(lambda: window.history.commits and window.history.commits[0].is_merge, timeout=5000)
    assert not changes.banner.isVisibleTo(window)
    assert (repo.path / "auth.py").read_text() == "def oauth(user):\n    return 'feature'\n"


def test_stash_shows_on_stashes_page_and_applies(window, repo, qtbot):
    repo.write("auth.py", "stashed change\n")
    window.backend.stash_save("my wip")
    window.refresh()

    qtbot.waitUntil(lambda: len(window.stashes.list.rows()) == 1, timeout=5000)
    (row,) = window.stashes.list.rows()
    assert row.title == "my wip" and "on main" in row.subtitle
    qtbot.waitUntil(lambda: "+stashed change" in window.stashes.diff.toPlainText(), timeout=5000)
    window.stashes.action_requested.emit("pop", window.stashes.selected())
    assert (repo.path / "auth.py").read_text() == "stashed change\n"
    assert window.backend.stashes() == []


def test_file_history_compare_and_working_copy(window, repo, qtbot):
    view = window.file_history
    repo.write("auth.py", "working copy\n")
    view.show_file("auth.py")
    qtbot.waitUntil(lambda: view.model.rowCount() == 4, timeout=5000)
    assert (view.a, view.b) == (1, 0)  # last commit vs working copy
    qtbot.waitUntil(lambda: "working copy" in view.compare_text(), timeout=5000)
    assert "def oauth(user):" in view.compare_text()

    view.timeline.set_a(3)  # the oldest version, chosen on the timeline
    assert view.a == 3
    qtbot.waitUntil(lambda: "def basic_auth(user):" in view.compare_text(), timeout=5000)
    assert view.chip_a.text().startswith("A · ") and view.chip_b.text() == "B · Working copy"
    assert view.head_a.text().startswith("A · login.py at ")  # the name it had back then

    view.swap()
    assert (view.a, view.b) == (0, 3) and view.timeline.a == 0


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
    changes.composer.summary.setText("x" * 80)
    assert "80 / 72" in changes.composer.counter.text() and "shorter" in changes.composer.counter.text()
    changes.composer.description.setPlainText("Body text")
    assert changes.composer.message() == "x" * 80 + "\n\nBody text"


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
    assert window.blame.model.item(2, 1).text() == "Not committed"
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
    assert window.current_page() is window.content_search


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
        assert window.history.details.text.highlighter.added.background().color().name() == theme.DARK.add_bg
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QApplication

        assert QApplication.palette().color(QPalette.ColorRole.Base).name() == "#0d1117"
        window._set_theme("light")
        assert window.history.details.text.highlighter.added.background().color().name() == theme.LIGHT.add_bg
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
    qtbot.waitUntil(
        lambda: len(dash.cards) == 3 and dash.task is not None and "checking" not in dash.subtitle.text(), timeout=5000
    )
    chips = {Path(p).name: card.state.chip for p, card in dash.cards.items()}
    assert chips == {"missing": "Unavailable", "other": "Detached", "repo": "1 change"}

    dash.cards[str(other)].open_btn.click()
    assert window.backend.root.name == "other" and window.current_page() is window.changes
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
    assert window.changes.unstaged_paths() == [".gitignore"]


def test_worktrees_on_branches_page(window, tmp_path, qtbot):
    window.backend.add_worktree(str(tmp_path / "wt"), "wt-branch", new_branch=True)
    window.refresh()

    def worktrees():
        return [r for r in window.branches.list.rows() if r.kind == "worktree"]

    qtbot.waitUntil(lambda: len(worktrees()) == 2, timeout=5000)
    here, other = worktrees()
    assert here.chip == "This window" and here.current
    assert other.title == "wt-branch"
    actions = {a.key: a.enabled for a in window.branches.actions(other)}
    assert actions == {"open": True, "remove_worktree": True}


# ---------------------------------------------------------------- non-UTF-8 files


@pytest.fixture
def latin1_conflict(window, repo):
    (repo.path / "l.txt").write_bytes(b"caf\xe9 = 1\nline2\n")
    repo.commit("latin1 base")
    repo.git("switch", "-q", "-c", "side")
    (repo.path / "l.txt").write_bytes(b"caf\xe9 = 1\nside\n")
    repo.commit("side")
    repo.git("switch", "-q", "main")
    (repo.path / "l.txt").write_bytes(b"caf\xe9 = 1\nmain\n")
    repo.commit("main")
    assert window.backend.merge("side") == ["l.txt"]
    return repo


def test_conflict_editor_keeps_latin1_bytes(window, latin1_conflict):
    from gitops.core.conflicts import Choice
    from gitops.ui.dialogs.conflict_editor import ConflictResolver

    resolver = ConflictResolver(window.backend, "l.txt", window)
    assert resolver.current.encoding == "latin-1" and "Not UTF-8" in resolver.encoding_note.text()
    assert "café = 1" in resolver.current.text  # readable, not a replacement character
    resolver.decide_all(Choice.THEIRS)
    resolver.mark_resolved()
    assert (latin1_conflict.path / "l.txt").read_bytes() == b"caf\xe9 = 1\nside\n"
    assert window.backend.conflicted_paths() == []


def test_conflict_editor_refuses_unencodable_text(window, latin1_conflict, monkeypatch):
    from gitops.ui.dialogs import conflict_editor
    from gitops.ui.dialogs.conflict_editor import ConflictResolver

    warnings = []
    monkeypatch.setattr(conflict_editor.QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    resolver = ConflictResolver(window.backend, "l.txt", window)
    resolver.decide(0, "main\n日本")  # typed by hand
    resolver.mark_resolved()
    assert warnings and "can't be stored" in warnings[0]
    assert window.backend.conflicted_paths() == ["l.txt"]  # nothing was written or staged


def test_conflict_editor_utf8_file_unchanged_behaviour(window, repo):
    from gitops.ui.dialogs.conflict_editor import ConflictResolver

    repo.write("u.txt", "naïve\nbase\n")
    repo.commit("utf8 base")
    repo.git("switch", "-q", "-c", "side2")
    repo.write("u.txt", "naïve\nside\n")
    repo.commit("side2")
    repo.git("switch", "-q", "main")
    repo.write("u.txt", "naïve\nmain\n")
    repo.commit("main2")
    window.backend.merge("side2")
    resolver = ConflictResolver(window.backend, "u.txt", window)
    assert resolver.current.encoding == "utf-8" and resolver.encoding_note.text() == ""
    resolver.decide(0, "main\n日本")
    resolver.mark_resolved()
    assert (repo.path / "u.txt").read_text(encoding="utf-8") == "naïve\nmain\n日本\n"


def test_changes_view_stages_line_in_latin1_file(window, repo):
    (repo.path / "l.txt").write_bytes(b"caf\xe9 = 1\nline2\n")
    repo.commit("latin1")
    (repo.path / "l.txt").write_bytes(b"caf\xe9 = 1\nLINE2\nextra\n")
    window._refresh_status()
    changes = window.changes
    changes.select_file("l.txt", staged=False)
    assert "café = 1" in changes.current_text()
    tick_line(changes, "+extra")
    changes.apply_selected_lines()
    assert window.backend.run_bytes("show", ":l.txt") == b"caf\xe9 = 1\nline2\nextra\n"
