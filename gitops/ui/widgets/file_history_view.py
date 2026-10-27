from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gitops.core.compare import is_binary, unified_diff
from gitops.core.file_history import file_history, version_content, working_copy
from gitops.core.git_backend import GitBackend
from gitops.core.models import ChangeKind, FileHistoryEntry
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui.widgets.common import item, kind_item, make_table, relative_time
from gitops.ui.widgets.diff_view import DiffView


class FileHistoryView(QWidget):
    """Every commit in which one file was created, modified, renamed or deleted."""

    open_blame = Signal(str, object)  # (path, rev or None)

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
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.selectionModel().currentRowChanged.connect(self._on_row)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_actions())
        self.table.setToolTip("Select two rows to compare those versions")

        # Timeline: oldest on the left, newest on the right.
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setEnabled(False)
        self.timeline.valueChanged.connect(self._on_timeline)
        self.timeline_label = QLabel()
        self.timeline_label.setMinimumWidth(120)

        self.change_btn = QPushButton("Change in commit")
        self.change_btn.clicked.connect(lambda: self._on_row(self.table.currentIndex(), None))
        self.vs_working_btn = QPushButton("Diff vs working copy")
        self.vs_working_btn.clicked.connect(self._diff_vs_working)
        self.compare_btn = QPushButton("Compare selected")
        self.compare_btn.clicked.connect(self._compare_selected)
        self.open_btn = QPushButton("Open")
        self.open_btn.clicked.connect(self._open_version)
        self.save_btn = QPushButton("Save as…")
        self.save_btn.clicked.connect(self._save_version)
        self.blame_btn = QPushButton("Blame")
        self.blame_btn.setToolTip("Who wrote each line, as of this version")
        self.blame_btn.clicked.connect(self._blame_version)
        self.restore_btn = QPushButton("Restore…")
        self.restore_btn.clicked.connect(self._restore)
        actions = QHBoxLayout()
        actions.addWidget(QLabel("Timeline"))
        actions.addWidget(self.timeline, 1)
        actions.addWidget(self.timeline_label)
        for button in (self.change_btn, self.vs_working_btn, self.compare_btn, self.open_btn, self.save_btn, self.blame_btn, self.restore_btn):
            actions.addWidget(button)

        self.diff = DiffView()
        split = QSplitter(Qt.Orientation.Vertical, objectName="file_history_split")
        split.addWidget(self.table)
        split.addWidget(self.diff)
        split.setSizes([260, 340])

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(self.summary)
        layout.addLayout(actions)
        layout.addWidget(split, 1)
        self._update_actions()

    def set_backend(self, backend: GitBackend | None) -> None:
        self.backend = backend
        self.entries = []
        self.timeline.setEnabled(False)
        self.timeline_label.clear()
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
        self.timeline.blockSignals(True)
        self.timeline.setRange(0, max(len(entries) - 1, 0))
        self.timeline.setValue(len(entries) - 1)
        self.timeline.setEnabled(len(entries) > 1)
        self.timeline.blockSignals(False)
        if entries:
            self.summary.setText(
                f"{len(entries)} commits · created {relative_time(entries[-1].commit.date)} "
                f"by {entries[-1].commit.author} · last changed {relative_time(entries[0].commit.date)}"
            )
            self.table.selectRow(0)
        else:
            self.summary.setText("No commits touch this path.")

    # ------------------------------------------------------------------ selection

    def _current(self) -> FileHistoryEntry | None:
        row = self.table.currentIndex().row()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def _update_actions(self) -> None:
        e = self._current()
        has = e is not None
        for button in (self.change_btn, self.vs_working_btn, self.open_btn, self.save_btn):
            button.setEnabled(has)
        self.blame_btn.setEnabled(has and e.kind is not ChangeKind.DELETED)
        self.restore_btn.setEnabled(has and e.kind is not ChangeKind.DELETED)
        self.compare_btn.setEnabled(len(self._selected_rows()) == 2)

    def _on_timeline(self, value: int) -> None:
        row = len(self.entries) - 1 - value
        if 0 <= row < len(self.entries):
            self.table.selectRow(row)

    def _on_row(self, current, _previous) -> None:
        if not current.isValid() or not self.backend:
            return
        row = current.row()
        e = self.entries[row]
        self.timeline.blockSignals(True)
        self.timeline.setValue(len(self.entries) - 1 - row)
        self.timeline.blockSignals(False)
        self.timeline_label.setText(f"{e.commit.short_sha} · {e.commit.date:%Y-%m-%d}")
        paths = [p for p in (e.old_path, e.path) if p]
        backend, sha = self.backend, e.commit.sha
        self.diff.show_async(lambda: backend.commit_diff(sha, *paths))
        self._update_actions()

    # ------------------------------------------------------------------ comparisons

    def _diff_vs_working(self) -> None:
        e, backend, current_path = self._current(), self.backend, self.path_edit.text().strip()
        if not e or not backend:
            return
        self.diff.show_async(
            lambda: unified_diff(
                version_content(backend, e),
                working_copy(backend, current_path),
                f"{e.path} @ {e.commit.short_sha}",
                f"{current_path} (working copy)",
            )
        )

    def _compare_selected(self) -> None:
        rows = self._selected_rows()
        if len(rows) != 2 or not self.backend:
            return
        newer, older = self.entries[rows[0]], self.entries[rows[1]]
        backend = self.backend
        self.diff.show_async(
            lambda: unified_diff(
                version_content(backend, older),
                version_content(backend, newer),
                f"{older.path} @ {older.commit.short_sha}",
                f"{newer.path} @ {newer.commit.short_sha}",
            )
        )

    # ------------------------------------------------------------------ versions

    def _open_version(self) -> None:
        e = self._current()
        if not e or not self.backend:
            return
        data = version_content(self.backend, e)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{e.path} @ {e.commit.short_sha} (read-only)")
        dialog.resize(800, 600)
        viewer = QPlainTextEdit(readOnly=True)
        viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        viewer.setFont(self.diff.font())
        if data is None:
            viewer.setPlainText("This file doesn't exist at this commit.")
        elif is_binary(data):
            viewer.setPlainText(f"Binary file, {len(data):,} bytes. Use Save as… to get a copy.")
        else:
            viewer.setPlainText(data.decode("utf-8", errors="replace"))
        QVBoxLayout(dialog).addWidget(viewer)
        dialog.show()

    def _save_version(self) -> None:
        e = self._current()
        if not e or not self.backend:
            return
        data = version_content(self.backend, e)
        if data is None:
            QMessageBox.information(self, "Save version", "This file doesn't exist at this commit.")
            return
        name = Path(e.path)
        suggested = str(Path.home() / f"{name.stem}@{e.commit.short_sha}{name.suffix}")
        dest, _ = QFileDialog.getSaveFileName(self, "Save this version as", suggested)
        if dest:
            Path(dest).write_bytes(data)

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
        e = self._current()
        if not e or not self.backend:
            return
        target = self.path_edit.text().strip()
        answer = QMessageBox.question(
            self,
            "Restore file",
            f"Overwrite {target} in your working tree with the version from {e.commit.short_sha}?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            dest = self.backend.root / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(version_content(self.backend, e) or b"")

    def _blame_version(self) -> None:
        e = self._current()
        if e:
            self.open_blame.emit(e.path, e.commit.sha)
