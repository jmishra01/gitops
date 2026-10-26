from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
)

from gitops.core.git_backend import GitBackend, GitError
from gitops.core.models import Commit
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui.widgets.common import item, make_table, relative_time
from gitops.ui.widgets.content_search_view import ContentSearchView
from gitops.ui.widgets.diff_view import DiffView
from gitops.ui.widgets.file_history_view import FileHistoryView


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GitOps")
        self.resize(1280, 820)
        self.settings = QSettings()  # scoped by the app's organization/application name
        self.runner = TaskRunner(self)
        self.backend: GitBackend | None = None
        self.commits: list[Commit] = []

        self._build_actions()

        self.sidebar = QTreeWidget()
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setMinimumWidth(200)

        self.history_table, self.history_model = make_table(["Commit", "Message", "Author", "When"], 1)
        self.history_table.selectionModel().currentRowChanged.connect(self._on_commit)
        self.commit_diff = DiffView()
        history = QSplitter(Qt.Orientation.Vertical)
        history.addWidget(self.history_table)
        history.addWidget(self.commit_diff)
        history.setSizes([320, 400])

        self.file_history = FileHistoryView(self.runner)
        self.content_search = ContentSearchView(self.runner)
        self.content_search.open_file_history.connect(self._open_file_history)

        self.tabs = QTabWidget()
        self.tabs.addTab(history, "History")
        self.tabs.addTab(self.file_history, "File history")
        self.tabs.addTab(self.content_search, "Search history")

        split = QSplitter()
        split.addWidget(self.sidebar)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([220, 1060])
        self.setCentralWidget(split)

        self.branch_label = QLabel()
        self.statusBar().addPermanentWidget(self.branch_label)

        last = self.settings.value("last_repo")
        if last and GitBackend.is_repo(last):
            self.open_repo(last)
        else:
            self.statusBar().showMessage("Open a repository to get started (Ctrl+O)")

    def _build_actions(self) -> None:
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)

        def add(text: str, shortcut, slot) -> QAction:
            action = QAction(text, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(slot)
            toolbar.addAction(action)
            return action

        add("Open repository", QKeySequence.StandardKey.Open, self._choose_repo)
        add("Refresh", QKeySequence.StandardKey.Refresh, self.refresh)
        toolbar.addSeparator()
        add("File history", "Ctrl+Shift+H", lambda: self.tabs.setCurrentWidget(self.file_history))
        add("Search history", "Ctrl+Shift+F", self._focus_search)

    # ------------------------------------------------------------------ repo

    def _choose_repo(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open repository")
        if path:
            self.open_repo(path)

    def open_repo(self, path: str) -> None:
        try:
            backend = GitBackend(path)
        except GitError:
            QMessageBox.warning(self, "Not a repository", f"{path} isn't a git repository.")
            return
        self.runner.cancel_all()
        self.backend = backend
        self.settings.setValue("last_repo", str(backend.root))
        self.setWindowTitle(f"GitOps — {backend.root.name}")
        self.file_history.set_backend(backend)
        self.content_search.set_backend(backend)
        self.refresh()

    def refresh(self) -> None:
        if not self.backend:
            return
        task = Task(lambda b=self.backend: (b.branches(), b.current_branch(), b.log(max_count=500)))
        task.signals.result.connect(self._populate)
        task.signals.error.connect(lambda msg: self.statusBar().showMessage(f"Couldn't read repository: {msg}"))
        self.runner.submit(task)

    def _populate(self, data) -> None:
        branches, current, commits = data
        self.sidebar.clear()
        local = QTreeWidgetItem(self.sidebar, ["Local branches"])
        remote = QTreeWidgetItem(self.sidebar, ["Remote branches"])
        for b in branches:
            node = QTreeWidgetItem(remote if b.is_remote else local, [("● " if b.is_current else "   ") + b.name])
            if b.upstream:
                node.setToolTip(0, f"Tracks {b.upstream}")
        self.sidebar.expandAll()

        self.commits = commits
        self.history_model.removeRows(0, self.history_model.rowCount())
        for c in commits:
            self.history_model.appendRow([
                item(c.short_sha, tooltip=c.sha),
                item(c.subject),
                item(c.author, tooltip=c.email),
                item(relative_time(c.date), tooltip=c.date.strftime("%Y-%m-%d %H:%M")),
            ])
        if commits:
            self.history_table.selectRow(0)
        self.branch_label.setText(f"  {current or 'detached HEAD'}  ")
        self.statusBar().showMessage(f"{self.backend.root}  ·  {len(commits)} commits loaded", 5000)

    def _on_commit(self, current, _previous) -> None:
        if current.isValid() and self.backend:
            self.commit_diff.show_diff(self.backend.commit_diff(self.commits[current.row()].sha))

    def _open_file_history(self, path: str) -> None:
        self.tabs.setCurrentWidget(self.file_history)
        self.file_history.show_file(path)

    def _focus_search(self) -> None:
        self.tabs.setCurrentWidget(self.content_search)
        self.content_search.query.setFocus()
        self.content_search.query.selectAll()

    def closeEvent(self, event) -> None:
        self.runner.cancel_all()
        super().closeEvent(event)
