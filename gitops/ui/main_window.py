from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
)

from gitops.core.git_backend import GitBackend, GitError
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui.dialogs.clone_dialog import CloneDialog
from gitops.ui.widgets.changes_view import ChangesView
from gitops.ui.widgets.content_search_view import ContentSearchView
from gitops.ui.widgets.file_history_view import FileHistoryView
from gitops.ui.widgets.history_view import HistoryView

BRANCH_ROLE = Qt.ItemDataRole.UserRole  # (name, is_remote, is_current)
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
        self.file_history = FileHistoryView(self.runner)
        self.content_search = ContentSearchView(self.runner)
        for view in (self.changes, self.content_search):
            view.open_file_history.connect(self._open_file_history)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.changes, "Changes")
        self.tabs.addTab(self.history, "History")
        self.tabs.addTab(self.file_history, "File history")
        self.tabs.addTab(self.content_search, "Search history")

        split = QSplitter()
        split.addWidget(self.sidebar)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 1060])
        self.setCentralWidget(split)

        self.progress_label = QLabel()
        self.branch_label = QLabel()
        self.statusBar().addWidget(self.progress_label, 1)
        self.statusBar().addPermanentWidget(self.branch_label)

        self._build_actions()
        self._set_repo_actions_enabled(False)

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
        self.file_history_act = self._action(
            "File history", lambda: self.tabs.setCurrentWidget(self.file_history), "Ctrl+Shift+H"
        )
        self.search_act = self._action("Search history", self._focus_search, "Ctrl+Shift+F")
        self.repo_actions = [
            self.refresh_act, self.fetch_act, self.pull_act, self.push_act,
            self.branch_act, self.file_history_act, self.search_act,
        ]

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([self.open_act, self.clone_act, self.init_act])
        self.recent_menu = file_menu.addMenu("Open recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        file_menu.addSeparator()
        file_menu.addAction(self._action("Quit", self.close, QKeySequence.StandardKey.Quit))

        repo_menu = self.menuBar().addMenu("&Repository")
        repo_menu.addActions([self.fetch_act, self.pull_act, self.push_act])
        repo_menu.addSeparator()
        repo_menu.addActions([self.branch_act, self.refresh_act])
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addActions([self.file_history_act, self.search_act])

        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.addActions([self.open_act, self.clone_act])
        toolbar.addSeparator()
        toolbar.addActions([self.fetch_act, self.pull_act, self.push_act, self.branch_act, self.refresh_act])
        toolbar.addSeparator()
        toolbar.addActions([self.file_history_act, self.search_act])

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
        self.statusBar().clearMessage()
        self._set_repo_actions_enabled(True)
        self.history.set_backend(backend)
        self.file_history.set_backend(backend)
        self.content_search.set_backend(backend)
        self.changes.set_backend(backend)
        self.refresh()

    def refresh(self) -> None:
        if not self.backend:
            return
        self._refresh_status()
        backend, all_refs = self.backend, self.history.all_refs.isChecked()
        task = Task(lambda: (backend.branches(), backend.remotes(), backend.log(max_count=1000, all_refs=all_refs)))
        task.signals.result.connect(lambda data, b=backend: self._populate(b, data))
        task.signals.error.connect(lambda msg: self.statusBar().showMessage(f"Couldn't read repository: {msg}"))
        self.runner.submit(task)

    def _refresh_status(self) -> None:
        """Cheap refresh: working-tree status and the branch/ahead-behind label."""
        if not self.backend:
            return
        count = self.changes.refresh()
        self.tabs.setTabText(0, f"Changes ({count})" if count else "Changes")
        branch = self.backend.current_branch() or "detached HEAD"
        ab = self.backend.ahead_behind()
        sync = f"  ↑{ab[0]} ↓{ab[1]}" if ab else "  (no upstream)"
        self.branch_label.setText(f"  {branch}{sync}  ")

    def _populate(self, backend: GitBackend, data) -> None:
        if backend is not self.backend:
            return  # a different repo was opened while this was loading
        branches, remotes, commits = data
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
        self.sidebar.expandAll()
        self.history.populate(commits, remotes)

    # ------------------------------------------------------------------ branches

    def _branch_at(self, item: QTreeWidgetItem | None):
        return item.data(0, BRANCH_ROLE) if item else None

    def _on_branch_double_click(self, item: QTreeWidgetItem) -> None:
        data = self._branch_at(item)
        if data and not data[2]:
            self._checkout(data[0], data[1])

    def _branch_menu(self, pos) -> None:
        data = self._branch_at(self.sidebar.itemAt(pos))
        if not data or not self.backend:
            return
        name, is_remote, is_current = data
        menu = QMenu(self)
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
        task.signals.finished.connect(lambda: self._network_done(title, errors, on_success))
        self.network_task = self.runner.submit(task)

    def _network_done(self, title: str, errors: list[str], on_success) -> None:
        self.network_task = None
        self.progress_label.clear()
        self.clone_act.setEnabled(True)
        for action in (self.fetch_act, self.pull_act, self.push_act):
            action.setEnabled(self.backend is not None)
        if errors:
            detail = errors[0].split("): ", 1)[-1]
            hint = ""
            if "rejected" in detail or "non-fast-forward" in detail:
                hint = "\n\nThe remote has commits you don't have yet. Pull first, then push again."
            elif "Authentication failed" in detail or "could not read Username" in detail or "Permission denied" in detail:
                hint = "\n\nCheck your credentials or SSH key; GitOps uses the same setup as git in your terminal."
            QMessageBox.warning(self, f"{title} failed", detail + hint)
        else:
            self.statusBar().showMessage(f"{title} finished", 4000)
            if on_success:
                on_success()
        self.refresh()

    # ------------------------------------------------------------------ misc

    def _open_file_history(self, path: str) -> None:
        self.tabs.setCurrentWidget(self.file_history)
        self.file_history.show_file(path)

    def _focus_search(self) -> None:
        self.tabs.setCurrentWidget(self.content_search)
        self.content_search.query.setFocus()
        self.content_search.query.selectAll()

    def changeEvent(self, event) -> None:
        # Pick up edits made in other apps when the user comes back to the window.
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and self.backend:
            self._refresh_status()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        self.runner.cancel_all()
        super().closeEvent(event)
