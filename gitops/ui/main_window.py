from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QHBoxLayout,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gitops.core.git_backend import GitBackend, GitError
from gitops.core.models import Commit, Stash, Submodule, Tag, Worktree
from gitops.services.task_runner import Task, TaskRunner
from gitops.core.rebase import has_merges, rebase_plan, run_interactive_rebase
from gitops.ui import theme
from gitops.ui.dialogs.clone_dialog import CloneDialog
from gitops.ui.dashboard import DashboardWindow
from gitops.ui.dialogs.command_palette import CommandPalette
from gitops.ui.dialogs.gitignore_dialog import GitignoreDialog
from gitops.ui.dialogs.undo_dialog import UndoDialog
from gitops.ui.widgets.git_console import GitConsole
from gitops.ui.dialogs.rebase_dialog import RebaseDialog
from gitops.ui.dialogs.stash_dialogs import StashSaveDialog, StashViewDialog
from gitops.ui.widgets.blame_view import BlameView
from gitops.ui.widgets.changes_view import ChangesView
from gitops.ui.widgets.content_search_view import ContentSearchView
from gitops.ui.widgets.file_history_view import FileHistoryView
from gitops.ui.widgets.history_view import HistoryView
from gitops.ui.widgets.nav_rail import NavRail
from gitops.ui.widgets.top_bar import SyncState, TopBar

BRANCH_ROLE = Qt.ItemDataRole.UserRole  # (name, is_remote, is_current)
TAG_ROLE = Qt.ItemDataRole.UserRole + 1  # Tag
STASH_ROLE = Qt.ItemDataRole.UserRole + 2  # Stash
SUBMODULE_ROLE = Qt.ItemDataRole.UserRole + 3  # Submodule
WORKTREE_ROLE = Qt.ItemDataRole.UserRole + 4  # Worktree
MAX_RECENT = 10


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GitOps")
        self.resize(1280, 820)
        self.settings = QSettings()  # scoped by the app's organization/application name
        self.runner = TaskRunner(self)
        self.backend: GitBackend | None = None
        self.network_task: Task | None = None

        self.sidebar = QTreeWidget()
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setMinimumWidth(200)
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._branch_menu)
        self.sidebar.itemDoubleClicked.connect(self._on_branch_double_click)

        self.changes = ChangesView()
        self.changes.changed.connect(self.refresh)
        self.history = HistoryView()
        self.history.reload_requested.connect(self.refresh)
        self.history.load_more.connect(self._load_more_history)
        self.history.filter_requested.connect(self._apply_history_filter)
        self.history.commit_menu_requested.connect(self._commit_menu)
        self.blame = BlameView(self.runner)
        self.blame.show_commit.connect(self._show_in_history)
        self.file_history = FileHistoryView(self.runner)
        self.content_search = ContentSearchView(self.runner)
        for view in (self.changes, self.content_search):
            view.open_file_history.connect(self._open_file_history)
        self.file_history.open_blame.connect(self._open_blame)
        self.changes.open_blame.connect(self._open_blame)

        # Pages, switched by the nav rail. The branch sidebar shows everywhere except Changes,
        # where the top bar's branch switcher covers it and the file list needs the room.
        self.pages = QStackedWidget()
        self.page_keys = {
            "changes": self.changes, "history": self.history, "files": self.file_history,
            "blame": self.blame, "search": self.content_search,
        }
        for widget in self.page_keys.values():
            self.pages.addWidget(widget)

        self.top_bar = TopBar()
        self.top_bar.sync_requested.connect(self._sync)
        self.top_bar.search_requested.connect(self._open_palette)
        self.top_bar.repo_menu.aboutToShow.connect(self._fill_repo_menu)
        self.top_bar.branch_menu.aboutToShow.connect(self._fill_branch_menu)
        self.top_bar.undo_button.clicked.connect(self._open_undo)
        self.rail = NavRail()
        self.rail.page_selected.connect(lambda key: self.show_page(self.page_keys[key]))
        self.rail.dashboard_requested.connect(self._open_dashboard)
        self.changes.count_changed.connect(self._set_change_count)

        self.main_split = QSplitter(objectName="main_split")
        self.main_split.addWidget(self.sidebar)
        self.main_split.addWidget(self.pages)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([220, 1060])
        body = QWidget(objectName="AppBody")
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.rail)
        row.addWidget(self.main_split, 1)
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.top_bar)
        column.addLayout(row, 1)
        self.setCentralWidget(body)

        self.progress_label = QLabel()
        self.cancel_network_btn = QPushButton("Cancel")
        self.cancel_network_btn.setFlat(True)
        self.cancel_network_btn.hide()
        self.cancel_network_btn.clicked.connect(self._cancel_network)
        self.statusBar().addWidget(self.progress_label, 1)
        self.statusBar().addWidget(self.cancel_network_btn)
        self.show_page(self.changes)

        self.dashboard: DashboardWindow | None = None
        self.console = GitConsole()
        self.console_dock = QDockWidget("Git console", self, objectName="git_console")
        self.console_dock.setWidget(self.console)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.console_dock)
        self.console_dock.hide()

        self._build_actions()
        self.top_bar.console_button.toggled.connect(self.console_dock.setVisible)
        self.console_dock.visibilityChanged.connect(self.top_bar.console_button.setChecked)
        self._set_repo_actions_enabled(False)
        self._restore_layout()

        last = self.settings.value("last_repo")
        if last and GitBackend.is_repo(last):
            self.open_repo(last)
        else:
            self.statusBar().showMessage("Open, clone or create a repository to get started")

    # ------------------------------------------------------------------ actions

    def _action(self, text: str, slot, shortcut=None, tip: str | None = None) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setToolTip(tip)
        action.triggered.connect(slot)
        return action

    def _build_actions(self) -> None:
        self.open_act = self._action("Open…", self._choose_repo, QKeySequence.StandardKey.Open)
        self.clone_act = self._action("Clone…", self._clone, "Ctrl+Shift+O")
        self.init_act = self._action("New repository…", self._init_repo)
        self.refresh_act = self._action("Refresh", self.refresh, QKeySequence.StandardKey.Refresh)
        self.fetch_act = self._action("Fetch", lambda: self._network("Fetching", lambda b: b.fetch()), "Ctrl+Shift+T")
        self.pull_act = self._action("Pull", lambda: self._network("Pulling", lambda b: b.pull()), "Ctrl+Shift+L")
        self.push_act = self._action("Push", lambda: self._network("Pushing", lambda b: b.push()), "Ctrl+Shift+P")
        self.branch_act = self._action("New branch…", lambda: self._new_branch("HEAD"), "Ctrl+Shift+B")
        self.stash_act = self._action("Stash…", self._stash_save, "Ctrl+Shift+S", "Save your changes and clean the working tree")
        self.file_history_act = self._action(
            "File history", lambda: self.show_page(self.file_history), "Ctrl+Shift+H"
        )
        self.search_act = self._action("Search history", self._focus_search, "Ctrl+Shift+F")
        self.undo_act = self._action("Undo…", self._open_undo, "Ctrl+Alt+Z", "Restore the branch to an earlier point")
        self.palette_act = self._action("Command palette…", self._open_palette, "Ctrl+K")
        self.dashboard_act = self._action("Repositories dashboard", self._open_dashboard, "Ctrl+Shift+D")
        self.worktree_act = self._action("Add worktree…", self._add_worktree)
        self.gitignore_act = self._action("Edit .gitignore…", self._edit_gitignore)
        self.console_act = self.console_dock.toggleViewAction()
        self.console_act.setText("Git console")
        self.console_act.setShortcut(QKeySequence("Ctrl+`"))
        self.repo_actions = [
            self.refresh_act, self.fetch_act, self.pull_act, self.push_act,
            self.branch_act, self.stash_act, self.file_history_act, self.search_act, self.undo_act,
            self.worktree_act, self.gitignore_act,
        ]

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([self.open_act, self.clone_act, self.init_act, self.dashboard_act])
        self.recent_menu = file_menu.addMenu("Open recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        file_menu.addSeparator()
        file_menu.addAction(self._action("Quit", self.close, QKeySequence.StandardKey.Quit))

        repo_menu = self.menuBar().addMenu("&Repository")
        repo_menu.addActions([self.fetch_act, self.pull_act, self.push_act])
        repo_menu.addSeparator()
        repo_menu.addActions([self.branch_act, self.stash_act, self.undo_act])
        repo_menu.addSeparator()
        repo_menu.addActions([self.worktree_act, self.gitignore_act, self.refresh_act])
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addActions([self.palette_act, self.file_history_act, self.search_act, self.console_act])
        theme_menu = view_menu.addMenu("Theme")
        group = QActionGroup(self)
        current_theme = theme.mode()
        for mode_name in theme.MODES:
            act = QAction(mode_name.capitalize(), self, checkable=True, checked=mode_name == current_theme)
            act.triggered.connect(lambda _=False, m=mode_name: self._set_theme(m))
            group.addAction(act)
            theme_menu.addAction(act)

        # The top bar replaces the old toolbar; everything stays in the menus and the command palette.
        self.top_bar.sync_menu.addActions([self.fetch_act, self.pull_act, self.push_act])
        settings = self.top_bar.settings_menu
        settings.addMenu(theme_menu)
        settings.addActions([self.palette_act, self.console_act, self.gitignore_act])

    def _set_repo_actions_enabled(self, enabled: bool) -> None:
        for action in self.repo_actions:
            action.setEnabled(enabled)

    # ------------------------------------------------------------------ repositories

    def _choose_repo(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open repository")
        if path:
            self.open_repo(path)

    def _init_repo(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Create repository in folder")
        if not path:
            return
        if GitBackend.is_repo(path):
            QMessageBox.information(self, "Already a repository", f"{path} is already inside a git repository.")
        else:
            GitBackend.init(path)
        self.open_repo(path)

    def _clone(self) -> None:
        default_parent = str(Path(self.backend.root).parent if self.backend else Path.home())
        dialog = CloneDialog(default_parent, self)
        if dialog.exec() != CloneDialog.DialogCode.Accepted:
            return
        url, dest = dialog.url.text().strip(), dialog.destination
        self._network(f"Cloning {url}", lambda _b: GitBackend.clone(url, dest), on_success=lambda: self.open_repo(str(dest)))

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        recent = [p for p in self._recent() if Path(p).is_dir()]
        for path in recent:
            self.recent_menu.addAction(path, lambda p=path: self.open_repo(p))
        if not recent:
            self.recent_menu.addAction("No recent repositories").setEnabled(False)

    def _recent(self) -> list[str]:
        value = self.settings.value("recent_repos", [])
        return [value] if isinstance(value, str) else list(value or [])

    def open_repo(self, path: str) -> None:
        try:
            backend = GitBackend(path)
        except (GitError, OSError):
            QMessageBox.warning(self, "Not a repository", f"{path} isn't a git repository.")
            return
        self.runner.cancel_all()
        self.backend = backend
        root = str(backend.root)
        self.settings.setValue("last_repo", root)
        self.settings.setValue("recent_repos", ([root] + [p for p in self._recent() if p != root])[:MAX_RECENT])
        self.setWindowTitle(f"GitOps — {backend.root.name}")
        self.top_bar.set_repo(backend.root.name)
        self.statusBar().clearMessage()
        self._set_repo_actions_enabled(True)
        backend.command_listeners.append(self.console.listener)
        self.history.set_backend(backend)
        self.file_history.set_backend(backend)
        self.content_search.set_backend(backend)
        self.blame.set_backend(backend)
        self.changes.set_backend(backend)
        self.refresh()

    def refresh(self) -> None:
        if not self.backend:
            return
        self._refresh_status_async()
        backend, all_refs = self.backend, self.history.all_refs.isChecked()
        page = self.history.PAGE_SIZE
        task = Task(
            lambda: (
                backend.branches(),
                backend.remotes(),
                backend.log(max_count=page, all_refs=all_refs),
                backend.tags(),
                backend.stashes(),
                backend.submodules(),
                backend.worktrees(),
            )
        )
        task.signals.result.connect(lambda data, b=backend: self._populate(b, data))
        task.signals.error.connect(lambda msg: self.statusBar().showMessage(f"Couldn't read repository: {msg}"))
        self.runner.submit(task)

    def _refresh_status(self) -> None:
        """Cheap refresh: working-tree status and the branch/ahead-behind label."""
        if not self.backend:
            return
        self._set_change_count(self.changes.refresh())
        self._set_branch_label(self._branch_info(self.backend))

    @staticmethod
    def _branch_info(backend: GitBackend) -> tuple[str | None, tuple[int, int] | None, bool]:
        return backend.current_branch(), backend.ahead_behind(), bool(backend.remotes())

    def _refresh_status_async(self) -> None:
        """Same as _refresh_status, but every git call runs on a worker thread."""
        if not self.backend:
            return
        self.changes.refresh_async(self.runner, self._set_change_count)
        backend = self.backend
        task = Task(self._branch_info, backend)
        task.signals.result.connect(lambda info, b=backend: b is self.backend and self._set_branch_label(info))
        self.runner.submit(task)

    def _set_change_count(self, count: int) -> None:
        self.rail.set_badge(count)

    def _set_branch_label(self, info) -> None:
        branch, ab, has_remote = info
        self.top_bar.set_branch(branch)
        self.top_bar.set_sync(SyncState.from_repo(has_remote, ab, branch), self._sync_busy_label())

    def _sync_busy_label(self) -> str | None:
        return "Syncing…" if self.network_task is not None else None

    # ------------------------------------------------------------------ pages

    def show_page(self, widget: QWidget) -> None:
        self.pages.setCurrentWidget(widget)
        key = next((k for k, w in self.page_keys.items() if w is widget), None)
        if key:
            self.rail.select(key)
        self.sidebar.setVisible(widget is not self.changes)

    def current_page(self) -> QWidget:
        return self.pages.currentWidget()

    def _populate(self, backend: GitBackend, data) -> None:
        if backend is not self.backend:
            return  # a different repo was opened while this was loading
        branches, remotes, commits, tags, stashes, submodules, worktrees = data
        expanded = {
            self.sidebar.topLevelItem(i).text(0).split(" (")[0]
            for i in range(self.sidebar.topLevelItemCount())
            if self.sidebar.topLevelItem(i).isExpanded()
        }
        first_load = self.sidebar.topLevelItemCount() == 0
        self.sidebar.clear()
        local = QTreeWidgetItem(self.sidebar, ["Local branches"])
        remote_root = QTreeWidgetItem(self.sidebar, ["Remote branches"])
        remote_nodes = {r: QTreeWidgetItem(remote_root, [r]) for r in remotes}
        for b in branches:
            if b.is_remote:
                remote_name = b.name.split("/", 1)[0]
                parent = remote_nodes.get(remote_name) or remote_nodes.setdefault(
                    remote_name, QTreeWidgetItem(remote_root, [remote_name])
                )
                label = b.name.split("/", 1)[1] if "/" in b.name else b.name
            else:
                parent, label = local, b.name
            node = QTreeWidgetItem(parent, [label])
            node.setData(0, BRANCH_ROLE, (b.name, b.is_remote, b.is_current))
            if b.is_current:
                font = node.font(0)
                font.setBold(True)
                node.setFont(0, font)
                node.setText(0, f"● {label}")
            if b.upstream:
                node.setToolTip(0, f"Tracks {b.upstream}")
        tag_root = QTreeWidgetItem(self.sidebar, [f"Tags ({len(tags)})"])
        for tag in tags:
            node = QTreeWidgetItem(tag_root, [tag.name])
            node.setData(0, TAG_ROLE, tag)
            node.setToolTip(0, tag.message or ("Annotated tag" if tag.annotated else "Lightweight tag"))
        stash_root = QTreeWidgetItem(self.sidebar, [f"Stashes ({len(stashes)})"])
        for stash in stashes:
            node = QTreeWidgetItem(stash_root, [stash.message])
            node.setData(0, STASH_ROLE, stash)
            node.setToolTip(0, f"{stash.ref} · double-click to preview")
        if submodules:
            sub_root = QTreeWidgetItem(self.sidebar, [f"Submodules ({len(submodules)})"])
            for sub in submodules:
                node = QTreeWidgetItem(sub_root, [sub.path + ("" if sub.state == "ok" else f"  ({sub.state})")])
                node.setData(0, SUBMODULE_ROLE, sub)
                node.setToolTip(0, f"{sub.sha[:8]} {sub.describe}".strip())
        if len(worktrees) > 1:
            wt_root = QTreeWidgetItem(self.sidebar, [f"Worktrees ({len(worktrees)})"])
            for wt in worktrees:
                label = f"{wt.branch or 'detached'} · {Path(wt.path).name}" + ("  (this one)" if Path(wt.path) == backend.root else "")
                node = QTreeWidgetItem(wt_root, [label])
                node.setData(0, WORKTREE_ROLE, wt)
                node.setToolTip(0, wt.path)
        for i in range(self.sidebar.topLevelItemCount()):
            top = self.sidebar.topLevelItem(i)
            name = top.text(0).split(" (")[0]
            if name in expanded or (first_load and name in ("Local branches", "Remote branches")):
                top.setExpanded(True)
                for j in range(top.childCount()):
                    top.child(j).setExpanded(True)
        query = self.history.filter_edit.text().strip()
        if self.history.filtered and query:
            self.history.refs_delegate.remotes = set(remotes)
            self._apply_history_filter(query)
        else:
            self.history.populate(commits, remotes)

    def _apply_history_filter(self, query: str) -> None:
        if not self.backend:
            return
        if not query:
            self.refresh()
            return
        backend, all_refs = self.backend, self.history.all_refs.isChecked()
        task = Task(backend.search_commits, query, all_refs)
        task.signals.result.connect(lambda commits, b=backend: b is self.backend and self.history.show_filtered(commits, query))
        task.signals.error.connect(lambda msg: self.statusBar().showMessage(f"Search failed: {msg}", 5000))
        self.runner.submit(task)

    def _load_more_history(self, skip: int) -> None:
        if not self.backend:
            return
        backend, all_refs, page = self.backend, self.history.all_refs.isChecked(), self.history.PAGE_SIZE
        task = Task(lambda: backend.log(max_count=page, skip=skip, all_refs=all_refs))
        task.signals.result.connect(lambda commits, b=backend: b is self.backend and self.history.append(commits))
        self.runner.submit(task)

    # ------------------------------------------------------------------ branches

    def _branch_at(self, item: QTreeWidgetItem | None):
        return item.data(0, BRANCH_ROLE) if item else None

    def _on_branch_double_click(self, item: QTreeWidgetItem) -> None:
        if sub := item.data(0, SUBMODULE_ROLE):
            self._open_submodule(sub)
        elif wt := item.data(0, WORKTREE_ROLE):
            self.open_repo(wt.path)
        elif stash := item.data(0, STASH_ROLE):
            self._open_stash(stash)
        elif tag := item.data(0, TAG_ROLE):
            self._show_in_history(tag.target_sha)
        elif (data := self._branch_at(item)) and not data[2]:
            self._checkout(data[0], data[1])

    def _branch_menu(self, pos) -> None:
        item = self.sidebar.itemAt(pos)
        if not item or not self.backend:
            return
        if stash := item.data(0, STASH_ROLE):
            self._stash_menu(stash, pos)
            return
        if sub := item.data(0, SUBMODULE_ROLE):
            menu = QMenu(self)
            menu.addAction("Open submodule", lambda: self._open_submodule(sub))
            menu.addAction("Update all submodules", self._update_submodules)
            menu.exec(self.sidebar.viewport().mapToGlobal(pos))
            return
        if wt := item.data(0, WORKTREE_ROLE):
            self._worktree_menu(wt, pos)
            return
        if tag := item.data(0, TAG_ROLE):
            self._tag_menu(tag, pos)
            return
        data = self._branch_at(item)
        if not data:
            return
        name, is_remote, is_current = data
        menu = QMenu(self)
        current = self.backend.current_branch()
        if not is_current and current:
            menu.addAction(f"Merge {name} into {current}…", lambda: self._merge(name))
            menu.addAction(f"Rebase {current} onto {name}", lambda: self._stoppable_op(
                f"Rebased {current} onto {name}", lambda b: b.rebase_onto(name)))
            menu.addSeparator()
        if is_remote:
            menu.addAction("Check out as local branch", lambda: self._checkout(name, True))
        elif not is_current:
            menu.addAction(f"Check out {name}", lambda: self._checkout(name, False))
        menu.addAction("New branch from here…", lambda: self._new_branch(name))
        if not is_remote:
            menu.addAction("Rename…", lambda: self._rename_branch(name))
            delete = menu.addAction("Delete…", lambda: self._delete_branch(name))
            delete.setEnabled(not is_current)
        menu.exec(self.sidebar.viewport().mapToGlobal(pos))

    def _git(self, fn, success: str | None = None) -> bool:
        if not self.backend:
            return False
        try:
            fn(self.backend)
        except GitError as exc:
            QMessageBox.warning(self, "Git", exc.stderr or str(exc))
            return False
        finally:
            self.refresh()
        if success:
            self.statusBar().showMessage(success, 4000)
        return True

    def _checkout(self, name: str, is_remote: bool) -> None:
        if is_remote:
            self._git(lambda b: b.checkout_remote(name), f"Checked out {name}")
        else:
            self._git(lambda b: b.checkout(name), f"Switched to {name}")

    def _ask_branch_name(self, title: str, label: str, initial: str = "") -> str | None:
        while True:
            name, ok = QInputDialog.getText(self, title, label, text=initial)
            name = name.strip()
            if not ok or not name:
                return None
            if self.backend and self.backend.is_valid_branch_name(name):
                return name
            QMessageBox.warning(self, title, f"“{name}” isn't a valid branch name.")
            initial = name

    def _new_branch(self, start: str) -> None:
        name = self._ask_branch_name("New branch", f"Branch name (starting from {start}):")
        if name:
            self._git(lambda b: b.create_branch(name, start), f"Created and switched to {name}")

    def _rename_branch(self, old: str) -> None:
        new = self._ask_branch_name("Rename branch", f"New name for {old}:", old)
        if new and new != old:
            self._git(lambda b: b.rename_branch(old, new), f"Renamed {old} to {new}")

    def _delete_branch(self, name: str) -> None:
        if QMessageBox.question(self, "Delete branch", f"Delete the local branch {name}?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.backend.delete_branch(name)
        except GitError as exc:
            if "not fully merged" not in exc.stderr:
                QMessageBox.warning(self, "Git", exc.stderr)
                return
            answer = QMessageBox.warning(
                self,
                "Unmerged branch",
                f"{name} has commits that aren't merged anywhere. Deleting it may lose them.\n\nDelete anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._git(lambda b: b.delete_branch(name, force=True), f"Deleted {name}")
            return
        self.refresh()
        self.statusBar().showMessage(f"Deleted {name}", 4000)

    # ------------------------------------------------------------------ network

    def _network(self, title: str, fn, on_success=None) -> None:
        """Run fetch/pull/push/clone in the background, streaming git's progress."""
        if self.network_task is not None:
            self.statusBar().showMessage("Another network operation is still running", 3000)
            return
        backend = self.backend
        for action in (self.fetch_act, self.pull_act, self.push_act, self.clone_act):
            action.setEnabled(False)
        self.progress_label.setText(f"{title}…")
        task = Task(fn, backend)
        errors: list[str] = []
        task.signals.items.connect(lambda lines: self.progress_label.setText(f"{title}: {lines[-1]}"))
        task.signals.error.connect(errors.append)
        task.signals.finished.connect(lambda: self._network_done(title, errors, on_success, task.cancelled))
        self.network_task = self.runner.submit(task)
        self.cancel_network_btn.show()
        self.top_bar.set_sync(self.top_bar.sync_state, "Syncing…")

    def _cancel_network(self) -> None:
        if self.network_task:
            self.network_task.cancel()
            self.progress_label.setText("Cancelling…")

    def _network_done(self, title: str, errors: list[str], on_success, cancelled: bool = False) -> None:
        self.network_task = None
        self.progress_label.clear()
        self.cancel_network_btn.hide()
        self.top_bar.set_sync(self.top_bar.sync_state)
        self.clone_act.setEnabled(True)
        for action in (self.fetch_act, self.pull_act, self.push_act):
            action.setEnabled(self.backend is not None)
        if cancelled:
            self.statusBar().showMessage(f"{title} cancelled", 4000)
        elif errors:
            detail = errors[0].split("): ", 1)[-1]
            hint = ""
            if "rejected" in detail or "non-fast-forward" in detail:
                hint = "\n\nThe remote has commits you don't have yet. Pull first, then push again."
            elif "Host key verification failed" in detail:
                hint = (
                    "\n\nThis server's SSH host key isn't trusted yet. Connect once from a terminal "
                    "(for example `ssh -T git@github.com`) to accept it, then try again."
                )
            elif "Permission denied (publickey" in detail or "passphrase" in detail.lower():
                hint = (
                    "\n\nYour SSH key wasn't available. If it has a passphrase, load it into the agent first "
                    "(`ssh-add`, or `ssh-add --apple-use-keychain` on macOS); GitOps can't type it for you."
                )
            elif "Authentication failed" in detail or "could not read Username" in detail or "Permission denied" in detail:
                hint = (
                    "\n\nCheck your credentials. For HTTPS, set up a credential helper (e.g. `gh auth setup-git`); "
                    "GitOps uses the same setup as git in your terminal."
                )
            QMessageBox.warning(self, f"{title} failed", detail + hint)
        else:
            self.statusBar().showMessage(f"{title} finished", 4000)
            if on_success:
                on_success()
        self.refresh()

    # ------------------------------------------------------------------ misc

    def _open_file_history(self, path: str) -> None:
        self.show_page(self.file_history)
        self.file_history.show_file(path)

    def _focus_search(self) -> None:
        self.show_page(self.content_search)
        self.content_search.query.setFocus()
        self.content_search.query.selectAll()

    def changeEvent(self, event) -> None:
        # Pick up edits made in other apps when the user comes back to the window.
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and self.backend:
            self._refresh_status_async()  # never block the window while git status runs
        super().changeEvent(event)

    def _restore_layout(self) -> None:
        if geometry := self.settings.value("window/geometry"):
            self.restoreGeometry(geometry)
        if state := self.settings.value("window/state"):
            self.restoreState(state)
        for split in self.findChildren(QSplitter):
            if split.objectName() and (state := self.settings.value(f"splitter/{split.objectName()}")):
                split.restoreState(state)

    def _save_layout(self) -> None:
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        for split in self.findChildren(QSplitter):
            if split.objectName():
                self.settings.setValue(f"splitter/{split.objectName()}", split.saveState())

    def closeEvent(self, event) -> None:
        self.runner.cancel_all()
        self._save_layout()
        super().closeEvent(event)

    # ------------------------------------------------------------------ tags and history

    def _show_in_history(self, sha: str) -> None:
        self.show_page(self.history)
        if not self.history.select_sha(sha):
            self.statusBar().showMessage("That commit isn't loaded; try All branches", 4000)

    def _tag_menu(self, tag: Tag, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Show in history", lambda: self._show_in_history(tag.target_sha))
        menu.addAction("New branch from here…", lambda: self._new_branch(tag.name))
        menu.addSeparator()
        remotes = self.backend.remotes() if self.backend else []
        for remote in remotes:
            menu.addAction(
                f"Push to {remote}", lambda r=remote: self._network(f"Pushing tag {tag.name}", lambda b: b.push_tag(tag.name, r))
            )
        menu.addAction("Delete…", lambda: self._delete_tag(tag))
        menu.exec(self.sidebar.viewport().mapToGlobal(pos))

    def _create_tag(self, target: str) -> None:
        name = self._ask_branch_name("New tag", f"Tag name for {target[:8]}:")
        if not name:
            return
        message, ok = QInputDialog.getText(self, "New tag", "Message (leave empty for a lightweight tag):")
        if ok:
            self._git(lambda b: b.create_tag(name, target, message.strip() or None), f"Tagged {target[:8]} as {name}")

    def _delete_tag(self, tag: Tag) -> None:
        if QMessageBox.question(self, "Delete tag", f"Delete the local tag {tag.name}?") == QMessageBox.StandardButton.Yes:
            self._git(lambda b: b.delete_tag(tag.name), f"Deleted tag {tag.name}")

    # ------------------------------------------------------------------ commit actions

    def _commit_menu(self, commit: Commit, global_pos) -> None:
        if not self.backend:
            return
        current = self.backend.current_branch() or "HEAD"
        menu = QMenu(self)
        menu.addAction("Copy SHA", lambda: QGuiApplication.clipboard().setText(commit.sha))
        menu.addSeparator()
        menu.addAction("New branch here…", lambda: self._new_branch(commit.sha))
        menu.addAction("New tag here…", lambda: self._create_tag(commit.sha))
        menu.addSeparator()
        menu.addAction(f"Cherry-pick onto {current}", lambda: self._stoppable_op(
            f"Cherry-picked {commit.short_sha}", lambda b: b.cherry_pick(commit.sha)))
        menu.addAction("Revert this commit", lambda: self._revert(commit))
        reset = menu.addMenu(f"Reset {current} to here")
        reset.addAction("Soft: keep changes staged", lambda: self._reset(commit, "soft"))
        reset.addAction("Mixed: keep changes unstaged", lambda: self._reset(commit, "mixed"))
        reset.addAction("Hard: discard all changes…", lambda: self._reset(commit, "hard"))
        menu.addSeparator()
        menu.addAction("Edit commits after this one…", lambda: self._interactive_rebase(commit.sha, commit.short_sha))
        if not commit.parents:
            menu.addAction("Edit all commits from the beginning…", lambda: self._interactive_rebase(None, "the start"))
        menu.exec(global_pos)

    def _stoppable_op(self, success: str, fn) -> None:
        conflicts: list[str] = []
        if not self._git(lambda b: conflicts.extend(fn(b))):
            return
        if conflicts:
            self.show_page(self.changes)
            QMessageBox.information(
                self,
                "Conflicts",
                f"{len(conflicts)} file(s) have conflicts. Resolve them in the Changes tab, then press Continue "
                "in the banner (or Abort to undo).",
            )
        else:
            self.statusBar().showMessage(success, 4000)

    def _revert(self, commit: Commit) -> None:
        mainline = 1 if commit.is_merge else None
        if commit.is_merge and QMessageBox.question(
            self, "Revert merge", "This is a merge. Revert it relative to its first parent (the branch it was merged into)?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self._stoppable_op(f"Reverted {commit.short_sha}", lambda b: b.revert(commit.sha, mainline))

    def _reset(self, commit: Commit, mode: str) -> None:
        if mode == "hard":
            answer = QMessageBox.warning(
                self,
                "Hard reset",
                f"Move the branch to {commit.short_sha} and throw away every uncommitted change in tracked files? "
                "This can't be undone from the working tree.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._git(lambda b: b.reset(commit.sha, mode), f"Reset to {commit.short_sha} ({mode})")

    def _interactive_rebase(self, base: str | None, label: str) -> None:
        if not self.backend:
            return
        if self.backend.operation_in_progress():
            QMessageBox.information(self, "Busy", "Finish or abort the operation shown in the Changes tab first.")
            return
        if any(s.is_staged or (s.is_unstaged and s.index != "?") for s in self.backend.status()):
            QMessageBox.information(self, "Uncommitted changes", "Commit or stash your changes before editing history.")
            return
        steps = rebase_plan(self.backend, base)
        if not steps:
            self.statusBar().showMessage("There are no commits after that one to edit", 4000)
            return
        dialog = RebaseDialog(steps, label, has_merges(self.backend, base), self)
        if dialog.exec() != RebaseDialog.DialogCode.Accepted:
            return
        chosen = dialog.steps()
        self._stoppable_op("History rewritten", lambda b: run_interactive_rebase(b, base, chosen))

    def _open_blame(self, path: str, rev: str | None) -> None:
        self.show_page(self.blame)
        self.blame.blame(path, rev)

    # ------------------------------------------------------------------ stash

    def _stash_save(self) -> None:
        if not self.backend:
            return
        dialog = StashSaveDialog(self)
        if dialog.exec() != StashSaveDialog.DialogCode.Accepted:
            return
        message = dialog.message.text().strip()
        untracked, keep = dialog.include_untracked.isChecked(), dialog.keep_index.isChecked()
        saved: list[bool] = []
        if self._git(lambda b: saved.append(b.stash_save(message, untracked, keep))):
            self.statusBar().showMessage("Changes stashed" if saved and saved[0] else "No local changes to stash", 4000)

    def _stash_menu(self, stash: Stash, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Preview…", lambda: self._open_stash(stash))
        menu.addAction("Apply", lambda: self._apply_stash(stash, pop=False))
        menu.addAction("Pop", lambda: self._apply_stash(stash, pop=True))
        menu.addSeparator()
        menu.addAction("Drop…", lambda: self._drop_stash(stash))
        menu.exec(self.sidebar.viewport().mapToGlobal(pos))

    def _open_stash(self, stash: Stash) -> None:
        if not self.backend:
            return
        dialog = StashViewDialog(self.backend, stash, self)
        dialog.exec()
        if dialog.chosen == StashViewDialog.DROP:
            self._drop_stash(stash)
        elif dialog.chosen:
            self._apply_stash(stash, pop=dialog.chosen == StashViewDialog.POP)

    def _apply_stash(self, stash: Stash, pop: bool) -> None:
        conflicts: list[str] = []
        if not self._git(lambda b: conflicts.extend(b.stash_apply(stash.ref, pop=pop))):
            return
        if conflicts:
            self.show_page(self.changes)
            QMessageBox.information(
                self,
                "Stash applied with conflicts",
                f"{len(conflicts)} file(s) conflict with your current work. Resolve them in the Changes tab."
                + ("\n\nThe stash was kept so nothing is lost." if pop else ""),
            )
        else:
            self.statusBar().showMessage(f"{'Popped' if pop else 'Applied'} {stash.ref}", 4000)

    def _drop_stash(self, stash: Stash) -> None:
        answer = QMessageBox.warning(
            self,
            "Drop stash",
            f"Delete “{stash.message}”? Its changes will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._git(lambda b: b.stash_drop(stash.ref), "Stash dropped")

    # ------------------------------------------------------------------ merge

    def _merge(self, name: str) -> None:
        if not self.backend:
            return
        current = self.backend.current_branch()
        box = QMessageBox(QMessageBox.Icon.Question, "Merge", f"Merge {name} into {current}?", parent=self)
        box.setStandardButtons(QMessageBox.StandardButton.Cancel)
        merge_btn = box.addButton("Merge", QMessageBox.ButtonRole.AcceptRole)
        no_ff = QCheckBox("Always create a merge commit (--no-ff)")
        box.setCheckBox(no_ff)
        box.exec()
        if box.clickedButton() is not merge_btn:
            return
        conflicts: list[str] = []
        if not self._git(lambda b: conflicts.extend(b.merge(name, no_ff=no_ff.isChecked()))):
            return
        if conflicts:
            self.show_page(self.changes)
            QMessageBox.information(
                self,
                "Merge conflicts",
                f"{len(conflicts)} file(s) have conflicts. Resolve each one in the Changes tab "
                "(double-click a file marked U), then commit to finish the merge. You can also abort the merge there.",
            )
        else:
            self.statusBar().showMessage(f"Merged {name} into {current}", 4000)

    # ------------------------------------------------------------------ polish

    def _set_theme(self, mode_name: str) -> None:
        self.settings.setValue("theme", mode_name)
        theme.apply(mode_name)

    def apply_theme(self) -> None:
        self.refresh()  # rebuild list items so their colors match the new theme

    def _palette_actions(self) -> list[tuple[str, QAction]]:
        found: list[tuple[str, QAction]] = []

        def walk(menu, prefix: str) -> None:
            for action in menu.actions():
                if action.isSeparator():
                    continue
                label = action.text().replace("&", "")
                if action.menu():
                    walk(action.menu(), f"{prefix}{label} › ")
                else:
                    found.append((f"{prefix}{label}", action))

        for top in self.menuBar().actions():
            if top.menu():
                walk(top.menu(), top.text().replace("&", "") + " › ")
        return found

    def _open_palette(self, text: str = "") -> None:
        palette = CommandPalette(self._palette_actions(), self)
        anchor = self.top_bar.search.mapToGlobal(self.top_bar.search.rect().bottomLeft())
        palette.move(anchor.x(), anchor.y() + 4)
        palette.show()
        palette.query.setText(text if isinstance(text, str) else "")
        palette.query.setFocus()

    def _open_undo(self) -> None:
        if not self.backend:
            return
        if UndoDialog(self.backend, self).exec():
            self.statusBar().showMessage("Branch restored", 4000)
        self.refresh()

    # ------------------------------------------------------------------ v0.3: repositories, worktrees, ignore

    def _open_dashboard(self) -> None:
        if self.dashboard is None:
            self.dashboard = DashboardWindow(self.runner, self)
            self.dashboard.open_repo.connect(self._open_from_dashboard)
        self.dashboard.show()
        self.dashboard.raise_()
        self.dashboard.refresh()

    def _open_from_dashboard(self, path: str) -> None:
        self.open_repo(path)
        self.raise_()
        self.activateWindow()

    def _open_submodule(self, sub: Submodule) -> None:
        if not self.backend:
            return
        if sub.state == "not initialized":
            QMessageBox.information(self, "Submodule", "This submodule isn't checked out yet. Use Update all submodules first.")
            return
        self.open_repo(str(self.backend.root / sub.path))

    def _update_submodules(self) -> None:
        self._network("Updating submodules", lambda b: b.update_submodules())

    def _worktree_menu(self, wt: Worktree, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Open", lambda: self.open_repo(wt.path))
        menu.addAction("Add worktree…", self._add_worktree)
        if not wt.is_main and self.backend and Path(wt.path) != self.backend.root:
            menu.addAction("Remove…", lambda: self._remove_worktree(wt))
        menu.exec(self.sidebar.viewport().mapToGlobal(pos))

    def _add_worktree(self) -> None:
        if not self.backend:
            return
        name = self._ask_branch_name("Add worktree", "New branch for the worktree:")
        if not name:
            return
        default = str(self.backend.root.parent / f"{self.backend.root.name}-{name.replace('/', '-')}")
        folder, ok = QInputDialog.getText(self, "Add worktree", "Folder for the new worktree:", text=default)
        if ok and folder.strip():
            if self._git(lambda b: b.add_worktree(folder.strip(), name, new_branch=True), f"Worktree created at {folder}"):
                if QMessageBox.question(self, "Worktree created", f"Open {folder} now?") == QMessageBox.StandardButton.Yes:
                    self.open_repo(folder.strip())

    def _remove_worktree(self, wt: Worktree) -> None:
        answer = QMessageBox.question(self, "Remove worktree", f"Remove the worktree at {wt.path}? The branch is kept.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.backend.remove_worktree(wt.path)
        except GitError as exc:
            if "modified or untracked" not in exc.stderr and "contains modified" not in exc.stderr:
                QMessageBox.warning(self, "Git", exc.stderr)
                return
            force = QMessageBox.warning(
                self,
                "Worktree has changes",
                "That worktree has uncommitted changes. Remove it anyway and lose them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if force != QMessageBox.StandardButton.Yes:
                return
            self._git(lambda b: b.remove_worktree(wt.path, force=True))
        self.refresh()

    def _edit_gitignore(self) -> None:
        if self.backend and GitignoreDialog(self.backend, self).exec():
            self.statusBar().showMessage(".gitignore saved", 4000)
            self.refresh()

    # ------------------------------------------------------------------ top bar

    def _sync(self) -> None:
        if not self.backend:
            self._choose_repo()
            return
        action = self.top_bar.sync_state.action
        if action == "none":
            QMessageBox.information(
                self, "No remote", "This repository has no remote yet. Add one with `git remote add origin <url>`."
            )
        elif action == "fetch":
            self._network("Fetching", lambda b: b.fetch())
        elif action == "pull":
            self._network("Pulling", lambda b: b.pull())
        elif action in ("push", "publish"):
            self._network("Pushing", lambda b: b.push())
        elif action == "sync":

            def pull_then_push(b: GitBackend):
                yield from b.pull()
                yield from b.push()

            self._network("Syncing", pull_then_push)

    def _fill_repo_menu(self) -> None:
        menu = self.top_bar.repo_menu
        menu.clear()
        current = str(self.backend.root) if self.backend else None
        recent = [p for p in self._recent() if Path(p).is_dir()]
        if recent:
            menu.addSection("Recent")
            for path in recent:
                act = menu.addAction(Path(path).name, lambda p=path: self.open_repo(p))
                act.setToolTip(path)
                act.setCheckable(True)
                act.setChecked(path == current)
        menu.addSeparator()
        menu.addActions([self.open_act, self.clone_act, self.init_act, self.dashboard_act])

    def _fill_branch_menu(self) -> None:
        menu = self.top_bar.branch_menu
        menu.clear()
        if not self.backend:
            menu.addAction("Open a repository first").setEnabled(False)
            return
        try:
            branches = [b for b in self.backend.branches() if not b.is_remote]
        except GitError:
            branches = []
        menu.addSection("Switch branch")
        for b in branches:
            act = menu.addAction(b.name, lambda n=b.name: self._checkout(n, False))
            act.setCheckable(True)
            act.setChecked(b.is_current)
            act.setEnabled(not b.is_current)
        menu.addSeparator()
        menu.addAction(self.branch_act)
        menu.addAction("All branches…", lambda: self.show_page(self.history))
