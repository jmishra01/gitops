from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gitops.core.file_history import file_history
from gitops.core.git_backend import GitBackend
from gitops.core.models import ChangeKind, FileHistoryEntry
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui.widgets.common import item, kind_item, make_table, relative_time
from gitops.ui.widgets.diff_view import DiffView


class FileHistoryView(QWidget):
    """Every commit in which one file was created, modified, renamed or deleted."""

    def __init__(self, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.backend: GitBackend | None = None
        self.entries: list[FileHistoryEntry] = []

        self.path_edit = QLineEdit(placeholderText="src/auth.py")
        self.path_edit.returnPressed.connect(self.load)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self.follow = QCheckBox("Follow renames", checked=True)
        self.all_refs = QCheckBox("All branches")
        load = QPushButton("Show history")
        load.setDefault(True)
        load.clicked.connect(self.load)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("File"))
        bar.addWidget(self.path_edit, 1)
        bar.addWidget(browse)
        bar.addWidget(self.follow)
        bar.addWidget(self.all_refs)
        bar.addWidget(load)

        self.summary = QLabel()
        self.summary.setStyleSheet("color: palette(placeholder-text);")

        self.table, self.model = make_table(["Change", "Commit", "Message", "Path", "+/-", "Author", "When"], 2)
        self.table.selectionModel().currentRowChanged.connect(self._on_row)

        self.restore_btn = QPushButton("Restore this version to working tree")
        self.restore_btn.setEnabled(False)
        self.restore_btn.clicked.connect(self._restore)

        self.diff = DiffView()
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.diff)
        split.setSizes([260, 340])

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.summary)
        layout.addWidget(split, 1)
        layout.addWidget(self.restore_btn, 0, Qt.AlignmentFlag.AlignRight)

    def set_backend(self, backend: GitBackend | None) -> None:
        self.backend = backend
        self.model.removeRows(0, self.model.rowCount())
        self.diff.clear()
        self.summary.clear()

    def show_file(self, path: str) -> None:
        self.path_edit.setText(path)
        self.load()

    def load(self) -> None:
        path = self.path_edit.text().strip()
        if not self.backend or not path:
            return
        self.set_backend(self.backend)
        self.summary.setText("Loading…")
        task = Task(file_history, self.backend, path, follow=self.follow.isChecked(), all_refs=self.all_refs.isChecked())
        task.signals.result.connect(self._populate)
        task.signals.error.connect(lambda msg: self.summary.setText(f"Couldn't load history: {msg}"))
        self.runner.submit(task)

    def _populate(self, entries: list[FileHistoryEntry]) -> None:
        self.entries = entries
        for e in entries:
            counts = "binary" if e.added is None else f"+{e.added} −{e.removed}"
            path = f"{e.old_path} → {e.path}" if e.old_path else e.path
            self.model.appendRow([
                kind_item(e.kind),
                item(e.commit.short_sha, tooltip=e.commit.sha),
                item(e.commit.subject),
                item(path),
                item(counts),
                item(e.commit.author, tooltip=e.commit.email),
                item(relative_time(e.commit.date), tooltip=e.commit.date.strftime("%Y-%m-%d %H:%M")),
            ])
        if entries:
            self.summary.setText(
                f"{len(entries)} commits · created {relative_time(entries[-1].commit.date)} "
                f"by {entries[-1].commit.author} · last changed {relative_time(entries[0].commit.date)}"
            )
            self.table.selectRow(0)
        else:
            self.summary.setText("No commits touch this path.")

    def _on_row(self, current, _previous) -> None:
        if not current.isValid() or not self.backend:
            return
        e = self.entries[current.row()]
        paths = [p for p in (e.old_path, e.path) if p]
        self.diff.show_diff(self.backend.commit_diff(e.commit.sha, *paths))
        self.restore_btn.setEnabled(e.kind is not ChangeKind.DELETED)

    def _browse(self) -> None:
        if not self.backend:
            return
        chosen, _ = QFileDialog.getOpenFileName(self, "Choose a file", str(self.backend.root))
        if chosen:
            try:
                self.show_file(Path(chosen).resolve().relative_to(self.backend.root.resolve()).as_posix())
            except ValueError:
                QMessageBox.warning(self, "Outside repository", "Pick a file inside the open repository.")

    def _restore(self) -> None:
        row = self.table.currentIndex().row()
        if row < 0 or not self.backend:
            return
        e = self.entries[row]
        target = self.path_edit.text().strip()
        answer = QMessageBox.question(
            self,
            "Restore file",
            f"Overwrite {target} in your working tree with the version from {e.commit.short_sha}?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            dest = self.backend.root / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self.backend.show_file(e.commit.sha, e.path))
