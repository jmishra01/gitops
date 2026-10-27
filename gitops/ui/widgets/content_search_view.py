from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gitops.core.content_search import SearchOptions, grep_at, iter_search, summarize
from gitops.core.git_backend import GitBackend
from gitops.core.models import SearchHit
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui.widgets.common import item, kind_item, make_table, relative_time
from gitops.ui.widgets.diff_view import DiffView


class ContentSearchView(QWidget):
    """Find text anywhere in history and show when it was added, changed or removed."""

    open_file_history = Signal(str)

    def __init__(self, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.backend: GitBackend | None = None
        self.hits: list[SearchHit] = []
        self.task: Task | None = None
        self.opts: SearchOptions | None = None

        self.query = QLineEdit(placeholderText="Text to find in any past version, e.g. basic_auth")
        self.query.returnPressed.connect(self.start)
        self.regex = QCheckBox("Regex")
        self.ignore_case = QCheckBox("Ignore case")
        self.all_refs = QCheckBox("All branches")
        self.paths = QLineEdit(placeholderText="Paths: *.py src/")
        self.paths.setMaximumWidth(180)
        self.author = QLineEdit(placeholderText="Author")
        self.author.setMaximumWidth(120)
        # Anything `git log --since` understands: 2024-01-31, "2 weeks ago", "yesterday".
        self.since = QLineEdit(placeholderText="Since: 2 weeks ago")
        self.since.setMaximumWidth(130)
        self.until = QLineEdit(placeholderText="Until: 2024-12-31")
        self.until.setMaximumWidth(130)
        for edit in (self.paths, self.author, self.since, self.until):
            edit.returnPressed.connect(self.start)
        self.search_btn = QPushButton("Search history")
        self.search_btn.setDefault(True)
        self.search_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)

        top = QHBoxLayout()
        top.addWidget(self.query, 1)
        top.addWidget(self.search_btn)
        top.addWidget(self.cancel_btn)
        filters = QHBoxLayout()
        for w in (self.regex, self.ignore_case, self.all_refs):
            filters.addWidget(w)
        filters.addStretch(1)
        filters.addWidget(self.paths)
        filters.addWidget(self.author)
        filters.addWidget(self.since)
        filters.addWidget(self.until)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color: palette(placeholder-text);")

        self.table, self.model = make_table(["Change", "Commit", "Match", "File", "Author", "When"], 2)
        self.table.selectionModel().currentRowChanged.connect(self._on_row)
        self.table.doubleClicked.connect(lambda idx: self.open_file_history.emit(self.hits[idx.row()].path))
        self.table.setToolTip("Double-click to open the file's full history")

        self.at_commit_btn = QPushButton("Where was it at this commit?")
        self.at_commit_btn.setToolTip("List every place the text appeared in the selected commit's snapshot")
        self.at_commit_btn.setEnabled(False)
        self.at_commit_btn.clicked.connect(self._show_at_commit)
        self.diff = DiffView()
        split = QSplitter(Qt.Orientation.Vertical, objectName="search_split")
        split.addWidget(self.table)
        split.addWidget(self.diff)
        split.setSizes([260, 340])

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(filters)
        summary_row = QHBoxLayout()
        summary_row.addWidget(self.summary, 1)
        summary_row.addWidget(self.at_commit_btn)
        layout.addLayout(summary_row)
        layout.addWidget(split, 1)

    def set_backend(self, backend: GitBackend | None) -> None:
        self.cancel()
        self.backend = backend
        self._clear()

    def _clear(self) -> None:
        self.at_commit_btn.setEnabled(False)
        self.hits = []
        self.model.removeRows(0, self.model.rowCount())
        self.diff.clear()
        self.summary.clear()

    def start(self) -> None:
        text = self.query.text()
        if not self.backend or not text:
            return
        self.cancel()
        self._clear()
        self.opts = SearchOptions(
            text=text,
            regex=self.regex.isChecked(),
            ignore_case=self.ignore_case.isChecked(),
            all_refs=self.all_refs.isChecked(),
            paths=self.paths.text().split(),
            author=self.author.text().strip() or None,
            since=self.since.text().strip() or None,
            until=self.until.text().strip() or None,
        )
        try:
            self.opts.python_pattern()
        except Exception as exc:
            self.summary.setText(f"That regex isn't valid: {exc}")
            return

        self.summary.setText("Searching history…")
        self.search_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        task = Task(iter_search, self.backend, self.opts)
        task.signals.items.connect(self._append)
        task.signals.error.connect(lambda msg: self.summary.setText(f"Search failed: {msg}"))
        task.signals.finished.connect(lambda t=task: self._finished(t))
        self.task = self.runner.submit(task)

    def cancel(self) -> None:
        if self.task:
            self.task.cancel()

    def _append(self, batch: list[SearchHit]) -> None:
        for hit in batch:
            self.hits.append(hit)
            self.model.appendRow([
                kind_item(hit.kind),
                item(hit.commit.short_sha, tooltip=f"{hit.commit.sha}\n{hit.commit.subject}"),
                item(hit.preview, tooltip=hit.commit.subject),
                item(hit.path),
                item(hit.commit.author, tooltip=hit.commit.email),
                item(relative_time(hit.commit.date), tooltip=hit.commit.date.strftime("%Y-%m-%d %H:%M")),
            ])
        if self.table.currentIndex().row() < 0 and self.hits:
            self.table.selectRow(0)
        self.summary.setText(f"Searching history… {len(self.hits)} matches so far")

    def _finished(self, task: Task) -> None:
        if task is not self.task:
            return
        self.search_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if task.cancelled:
            self.summary.setText(f"Cancelled · {len(self.hits)} matches found before stopping")
            return
        if not self.backend or not self.opts:
            return
        if self.summary.text().startswith("Search failed"):
            return
        s = summarize(self.backend, self.opts, self.hits)
        parts = [
            f"Now: {'present in ' + str(len({m.path for m in s.current_matches})) + ' file(s)' if s.present_now else 'not present'}",
            f"{s.commit_count} commits",
        ]
        if s.first_added:
            parts.append(f"first added {s.first_added.commit.short_sha} ({relative_time(s.first_added.commit.date)})")
        if s.last_removed:
            parts.append(f"last removed {s.last_removed.commit.short_sha} ({relative_time(s.last_removed.commit.date)})")
        self.summary.setText(" · ".join(parts) if self.hits or s.present_now else "No commit ever added or removed this text.")

    def _on_row(self, current, _previous) -> None:
        if not current.isValid() or not self.backend or not self.opts:
            return
        self.at_commit_btn.setEnabled(True)
        hit = self.hits[current.row()]
        backend = self.backend
        self.diff.show_async(lambda: backend.commit_diff(hit.commit.sha, hit.path), self.opts.python_pattern())

    def _show_at_commit(self) -> None:
        row = self.table.currentIndex().row()
        if row < 0 or not self.backend or not self.opts:
            return
        hit, backend, opts = self.hits[row], self.backend, self.opts

        def render() -> str:
            matches = grep_at(backend, opts, hit.commit.sha)
            header = f"Snapshot at {hit.commit.short_sha} ({hit.commit.subject})"
            if not matches:
                return f"{header}\n\nThe text doesn't appear anywhere in this snapshot."
            files = len({m.path for m in matches})
            lines = [f"{header}: {len(matches)} matches in {files} file(s)", ""]
            lines += [f"{m.path}:{m.line_number}: {m.text}" for m in matches]
            return "\n".join(lines)

        self.diff.show_async(render, opts.python_pattern())
