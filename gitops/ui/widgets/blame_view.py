from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontDatabase
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSplitter, QVBoxLayout, QWidget

from gitops.core.git_backend import GitBackend
from gitops.core.models import BlameLine
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui.theme import colors
from gitops.ui.widgets.common import item, make_table, relative_time
from gitops.ui.widgets.diff_view import DiffView



class BlameView(QWidget):
    """Who last changed each line of a file, and in which commit."""

    show_commit = Signal(str)  # sha, to open in History

    def __init__(self, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.backend: GitBackend | None = None
        self.lines: list[BlameLine] = []
        self.rev: str | None = None

        self.path_edit = QLineEdit(placeholderText="src/auth.py")
        self.path_edit.returnPressed.connect(lambda: self.blame(self.path_edit.text().strip(), None))
        self.rev_label = QLabel("Working tree")
        self.rev_label.setStyleSheet("color: palette(placeholder-text);")
        go = QPushButton("Blame")
        go.setDefault(True)
        go.clicked.connect(lambda: self.blame(self.path_edit.text().strip(), None))
        bar = QHBoxLayout()
        bar.addWidget(QLabel("File"))
        bar.addWidget(self.path_edit, 1)
        bar.addWidget(self.rev_label)
        bar.addWidget(go)

        self.table, self.model = make_table(["Commit", "Author", "When", "Line", "Code"], 4)
        self.table.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.setAlternatingRowColors(False)
        self.table.selectionModel().currentRowChanged.connect(self._on_row)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.diff = DiffView()

        split = QSplitter(Qt.Orientation.Vertical, objectName="blame_split")
        split.addWidget(self.table)
        split.addWidget(self.diff)
        split.setSizes([460, 240])
        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(split, 1)

    def set_backend(self, backend: GitBackend | None) -> None:
        self.backend = backend
        self.lines = []
        self.model.removeRows(0, self.model.rowCount())
        self.diff.clear()

    def blame(self, path: str, rev: str | None) -> None:
        if not self.backend or not path:
            return
        self.path_edit.setText(path)
        self.rev = rev
        self.rev_label.setText(f"At {rev[:8]}" if rev else "Working tree")
        self.model.removeRows(0, self.model.rowCount())
        self.diff.setPlainText("Loading blame…")
        task = Task(self.backend.blame, path, rev)
        task.signals.result.connect(self._populate)
        task.signals.error.connect(lambda msg: self.diff.setPlainText(f"Couldn't blame this file: {msg}"))
        self.runner.submit(task)

    def _populate(self, lines: list[BlameLine]) -> None:
        self.lines = lines
        self.model.removeRows(0, self.model.rowCount())
        theme = colors()
        committed = sorted({ln.timestamp for ln in lines if not ln.uncommitted})
        rank_of = {ts: n / max(len(committed) - 1, 1) for n, ts in enumerate(committed)}
        previous_sha = None
        for ln in lines:
            first_of_run = ln.sha != previous_sha
            previous_sha = ln.sha
            if ln.uncommitted:
                sha, author, when, color = "uncommitted", "You", "", theme.uncommitted_bg
            else:
                rank = rank_of[ln.timestamp]
                # Newer lines get a warmer tint so recent changes stand out at a glance.
                color = theme.age[min(int((1 - rank) * len(theme.age)), len(theme.age) - 1)]
                sha, author, when = ln.sha[:8], ln.author, relative_time(ln.date)
            row = [
                item(sha if first_of_run else "", tooltip=f"{ln.sha}\n{ln.summary}"),
                item(author if first_of_run else ""),
                item(when if first_of_run else ""),
                item(str(ln.line_number)),
                item(ln.text),
            ]
            for cell in row:
                cell.setBackground(QBrush(QColor(color)))
            self.model.appendRow(row)
        self.diff.setPlainText(f"{len(lines)} lines from {len({ln.sha for ln in lines})} commits. Click a line to see its commit.")

    def apply_theme(self) -> None:
        if self.lines:
            self._populate(self.lines)

    def _on_row(self, current, _previous) -> None:
        if not current.isValid() or not self.backend or current.row() >= len(self.lines):
            return
        ln = self.lines[current.row()]
        if ln.uncommitted:
            self.diff.setPlainText("This line has uncommitted changes.")
            return
        backend = self.backend
        self.diff.show_async(lambda: backend.commit_diff(ln.sha, ln.orig_path))

    def _menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid() or index.row() >= len(self.lines):
            return
        ln = self.lines[index.row()]
        if ln.uncommitted:
            return
        menu = QMenu(self)
        menu.addAction("Show commit in history", lambda: self.show_commit.emit(ln.sha))
        menu.addAction("Blame the version before this commit", lambda: self.blame(ln.orig_path, f"{ln.sha}^"))
        menu.exec(self.table.viewport().mapToGlobal(pos))
