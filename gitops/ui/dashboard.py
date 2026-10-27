from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from gitops.core.git_backend import GitBackend
from gitops.core.models import RepoSummary
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui.theme import colors
from gitops.ui.widgets.common import item, make_table, relative_time

PATH_ROLE = Qt.ItemDataRole.UserRole


def _summaries(paths: list[str]):
    for path in paths:
        yield GitBackend.summarize_repo(path)


def _fetch_all(paths: list[str]):
    """Fetch every repo in turn, yielding (path, progress line); one failure doesn't stop the rest."""
    for path in paths:
        name = Path(path).name
        try:
            backend = GitBackend(path)
            if not backend.remotes():
                yield path, f"{name}: no remote"
                continue
            for line in backend.fetch():
                yield path, f"{name}: {line}"
        except Exception as exc:  # reported in the status line for that repo
            yield path, f"{name}: failed: {str(exc).split('): ', 1)[-1][:200]}"


class DashboardWindow(QWidget):
    """Every repository you track, with branch, changes and sync state at a glance."""

    open_repo = Signal(str)
    SETTINGS_KEY = "dashboard/repos"

    def __init__(self, runner: TaskRunner, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("GitOps · Repositories")
        self.resize(1000, 520)
        self.runner = runner
        self.settings = QSettings()
        self.task: Task | None = None

        add = QPushButton("Add repository…")
        add.clicked.connect(self._add)
        scan = QPushButton("Scan folder…")
        scan.clicked.connect(self._scan)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self._remove)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self.fetch_btn = QPushButton("Fetch all")
        self.fetch_btn.clicked.connect(self.fetch_all)
        open_btn = QPushButton("Open")
        open_btn.setDefault(True)
        open_btn.clicked.connect(self._open_selected)
        bar = QHBoxLayout()
        for w in (add, scan, self.remove_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        for w in (refresh, self.fetch_btn, open_btn):
            bar.addWidget(w)

        self.table, self.model = make_table(["Repository", "Branch", "Changes", "Sync", "Stashes", "Last commit", "State"], 5)
        self.table.doubleClicked.connect(lambda _i: self._open_selected())
        self.status = QLabel()
        self.status.setStyleSheet("color: palette(placeholder-text);")

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)

    # ------------------------------------------------------------------ repo list

    def repos(self) -> list[str]:
        value = self.settings.value(self.SETTINGS_KEY, None)
        if value is None:
            recent = self.settings.value("recent_repos", [])
            value = [recent] if isinstance(recent, str) else list(recent or [])
        return [value] if isinstance(value, str) else list(value)

    def set_repos(self, paths: list[str]) -> None:
        unique = list(dict.fromkeys(str(Path(p)) for p in paths))
        self.settings.setValue(self.SETTINGS_KEY, sorted(unique, key=lambda p: Path(p).name.lower()))
        self.refresh()

    def _add(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add repository")
        if path and GitBackend.is_repo(path):
            self.set_repos(self.repos() + [str(GitBackend(path).root)])
        elif path:
            self.status.setText(f"{path} isn't a git repository.")

    def _scan(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Find repositories inside")
        if not folder:
            return
        found = GitBackend.find_repos(folder)
        self.status.setText(f"Found {len(found)} repositories in {folder}")
        self.set_repos(self.repos() + found)

    def _selected_paths(self) -> list[str]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.item(r, 0).data(PATH_ROLE) for r in rows]

    def _remove(self) -> None:
        drop = set(self._selected_paths())
        if drop:
            self.set_repos([p for p in self.repos() if p not in drop])

    def _open_selected(self) -> None:
        paths = self._selected_paths()
        if paths:
            self.open_repo.emit(paths[0])

    # ------------------------------------------------------------------ loading

    def refresh(self) -> None:
        if self.task:
            self.task.cancel()
        self.model.removeRows(0, self.model.rowCount())
        paths = self.repos()
        self.status.setText(f"Checking {len(paths)} repositories…" if paths else "Add or scan for repositories to track them here.")
        task = Task(_summaries, paths)
        task.signals.items.connect(self._add_rows)
        task.signals.finished.connect(lambda: self.status.setText(f"{self.model.rowCount()} repositories"))
        self.task = self.runner.submit(task)

    def _add_rows(self, summaries: list[RepoSummary]) -> None:
        c = colors()
        for s in summaries:
            if s.error:
                cells = [item(s.name, s.path, s.path), item("—"), item(""), item(""), item(""), item(""), item("Unavailable", tooltip=s.error)]
                cells[-1].setForeground(QBrush(QColor(c.danger)))
                self.model.appendRow(cells)
                continue
            sync = f"↑{s.ahead_behind[0]} ↓{s.ahead_behind[1]}" if s.ahead_behind else "no upstream"
            last = f"{s.last_commit.subject} · {relative_time(s.last_commit.date)}" if s.last_commit else "no commits"
            if s.conflicts:
                state, color = f"{s.conflicts} conflicts", c.danger
            elif s.operation:
                state, color = f"{s.operation} in progress", c.warning
            elif s.changed:
                state, color = "uncommitted changes", c.warning
            elif s.ahead_behind and s.ahead_behind[1]:
                state, color = "behind remote", c.accent
            else:
                state, color = "clean", c.success
            cells = [
                item(s.name, s.path, s.path),
                item(s.branch or "detached"),
                item(str(s.changed) if s.changed else ""),
                item(sync),
                item(str(s.stashes) if s.stashes else ""),
                item(last, tooltip=last),
                item(state),
            ]
            cells[-1].setForeground(QBrush(QColor(color)))
            self.model.appendRow(cells)

    def fetch_all(self) -> None:
        paths = self.repos()
        if not paths or (self.task and not self.task.cancelled and self.fetch_btn.text() == "Cancel"):
            if self.task:
                self.task.cancel()
            return
        self.fetch_btn.setText("Cancel")
        task = Task(_fetch_all, paths)
        task.signals.items.connect(lambda batch: self.status.setText(batch[-1][1]))
        task.signals.finished.connect(self._fetch_done)
        self.task = self.runner.submit(task)

    def _fetch_done(self) -> None:
        self.fetch_btn.setText("Fetch all")
        self.refresh()

    def apply_theme(self) -> None:
        if self.isVisible():
            self.refresh()
