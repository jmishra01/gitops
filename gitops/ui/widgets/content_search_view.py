from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from gitops.core.content_search import SearchOptions, grep_at, iter_search, summarize
from gitops.core.git_backend import GitBackend
from gitops.core.models import SearchHit
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui.theme import colors
from gitops.ui.widgets.common import item, kind_item, make_table, relative_time
from gitops.ui.widgets.diff_view import DiffView
from gitops.ui.widgets.history_view import paint_row_background

# kind label -> (chip background, chip text) theme attributes
KIND_CHIPS = {
    "Added": ("success_soft", "success_on_soft"),
    "Changed": ("warning_soft", "warning_on_soft"),
    "Removed": ("danger_soft", "danger_on_soft"),
}


class ResultDelegate(QStyledItemDelegate):
    """Results table cells: soft change chips, monospace SHAs, secondary text for the rest."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_row_background(painter, option)
        c = colors()
        text = str(index.data() or "")
        rect = option.rect.adjusted(10, 0, -10, 0)
        column = index.column()
        if column == 0:
            bg, fg = KIND_CHIPS.get(text, ("field_bg", "text"))
            font = QFont(option.font)
            font.setWeight(QFont.Weight.DemiBold)
            font.setPointSizeF(max(option.font.pointSizeF() - 1.5, 8))
            painter.setFont(font)
            chip = QRect(rect.left(), rect.center().y() - 10, painter.fontMetrics().horizontalAdvance(text) + 16, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(getattr(c, bg)))
            painter.drawRoundedRect(chip, 10, 10)
            painter.setPen(QColor(getattr(c, fg)))
            painter.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), text)
        else:
            if column in (1, 2):
                painter.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            selected = option.state & QStyle.StateFlag.State_Selected
            painter.setPen(QColor(c.text if column in (2, 3) or selected else c.text_secondary))
            align = Qt.AlignmentFlag.AlignVCenter | (
                Qt.AlignmentFlag.AlignRight if column == 5 else Qt.AlignmentFlag.AlignLeft
            )
            mode = Qt.TextElideMode.ElideMiddle if column == 3 else Qt.TextElideMode.ElideRight
            painter.drawText(rect, int(align), painter.fontMetrics().elidedText(text, mode, rect.width()))
        painter.restore()


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

        # ---- header: what to find
        title = QLabel("Search history", objectName="Title")
        self.query = QLineEdit(objectName="SearchField")
        self.query.setPlaceholderText("Text to find in any past version, e.g. basic_auth")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.start)
        self.search_btn = QPushButton("Search")
        self.search_btn.setProperty("variant", "primary")
        self.search_btn.setDefault(True)
        self.search_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setProperty("variant", "secondary")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self.cancel)
        top = QHBoxLayout()
        top.setContentsMargins(16, 12, 16, 6)
        top.setSpacing(10)
        top.addWidget(title)
        top.addSpacing(6)
        top.addWidget(self.query, 1)
        top.addWidget(self.search_btn)
        top.addWidget(self.cancel_btn)

        # ---- narrowing it down
        self.regex = QCheckBox("Regex")
        self.ignore_case = QCheckBox("Ignore case")
        self.all_refs = QCheckBox("All branches")
        self.paths = QLineEdit(placeholderText="Paths: *.py src/", objectName="SearchField")
        self.author = QLineEdit(placeholderText="Author", objectName="SearchField")
        # Anything `git log --since` understands: 2024-01-31, "2 weeks ago", "yesterday".
        self.since = QLineEdit(placeholderText="Since: 2 weeks ago", objectName="SearchField")
        self.until = QLineEdit(placeholderText="Until: 2024-12-31", objectName="SearchField")
        for edit, width in ((self.paths, 170), (self.author, 130), (self.since, 140), (self.until, 140)):
            edit.setMaximumWidth(width)
            edit.returnPressed.connect(self.start)
        filters = QHBoxLayout()
        filters.setContentsMargins(16, 0, 16, 12)
        filters.setSpacing(10)
        for w in (self.regex, self.ignore_case, self.all_refs):
            filters.addWidget(w)
        filters.addStretch(1)
        for w in (self.paths, self.author, self.since, self.until):
            filters.addWidget(w)

        # ---- results
        self.summary = QLabel(objectName="Muted")
        self.summary.setWordWrap(True)
        self.at_commit_btn = QPushButton("Where was it at this commit?")
        self.at_commit_btn.setProperty("variant", "secondary")
        self.at_commit_btn.setToolTip("List every place the text appeared in the selected commit's snapshot")
        self.at_commit_btn.setEnabled(False)
        self.at_commit_btn.clicked.connect(self._show_at_commit)
        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(16, 8, 16, 8)
        summary_row.addWidget(self.summary, 1)
        summary_row.addWidget(self.at_commit_btn)

        self.table, self.model = make_table(["Change", "Commit", "Match", "File", "Author", "When"], 2)
        self.table.setObjectName("CommitTable")
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setAlternatingRowColors(False)
        self.table.setMouseTracking(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setItemDelegate(ResultDelegate(self.table))
        header = self.table.horizontalHeader()
        header.hide()  # chips, SHAs, paths and times read fine without column titles
        for column, width in ((0, 96), (1, 86), (3, 220), (4, 130), (5, 90)):
            header.setSectionResizeMode(
                column, header.ResizeMode.Interactive if column == 3 else header.ResizeMode.Fixed
            )
            self.table.setColumnWidth(column, width)
        self.table.selectionModel().currentRowChanged.connect(self._on_row)
        self.table.doubleClicked.connect(lambda idx: self.open_file_history.emit(self.hits[idx.row()].path))
        self.table.setToolTip("Double-click to open the file's full history")

        results = QFrame(objectName="Panel")
        results.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        rl = QVBoxLayout(results)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addLayout(top)
        rl.addLayout(filters)
        for row in (None, summary_row):
            divider = QFrame(objectName="PanelDivider")
            divider.setFixedHeight(1)
            rl.addWidget(divider)
            if row is not None:
                rl.addLayout(row)
        rl.addWidget(self.table, 1)

        # ---- the selected change
        self.diff_title = QLabel(objectName="FileTitle")
        diff_header = QFrame(objectName="DiffHeader")
        diff_header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        dh = QHBoxLayout(diff_header)
        dh.setContentsMargins(16, 8, 16, 8)
        dh.addWidget(self.diff_title, 1)
        self.diff = DiffView()
        self.diff.setFrameShape(QFrame.Shape.NoFrame)
        diff_panel = QWidget()
        dl = QVBoxLayout(diff_panel)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(0)
        dl.addWidget(diff_header)
        dl.addWidget(self.diff, 1)

        split = QSplitter(Qt.Orientation.Vertical, objectName="search_split")
        split.addWidget(results)
        split.addWidget(diff_panel)
        split.setSizes([360, 320])
        split.setHandleWidth(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
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
        self.diff_title.clear()
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
        self.cancel_btn.show()
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
            self.model.appendRow(
                [
                    kind_item(hit.kind),
                    item(hit.commit.short_sha, tooltip=f"{hit.commit.sha}\n{hit.commit.subject}"),
                    item(hit.preview, tooltip=hit.commit.subject),
                    item(hit.path),
                    item(hit.commit.author, tooltip=hit.commit.email),
                    item(relative_time(hit.commit.date), tooltip=hit.commit.date.strftime("%Y-%m-%d %H:%M")),
                ]
            )
        if self.table.currentIndex().row() < 0 and self.hits:
            self.table.selectRow(0)
        self.summary.setText(f"Searching history… {len(self.hits)} matches so far")

    def _finished(self, task: Task) -> None:
        if task is not self.task:
            return
        self.search_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.hide()
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
            parts.append(
                f"last removed {s.last_removed.commit.short_sha} ({relative_time(s.last_removed.commit.date)})"
            )
        self.summary.setText(
            " · ".join(parts) if self.hits or s.present_now else "No commit ever added or removed this text."
        )

    def _on_row(self, current, _previous) -> None:
        if not current.isValid() or not self.backend or not self.opts:
            return
        self.at_commit_btn.setEnabled(True)
        hit = self.hits[current.row()]
        backend = self.backend
        self.diff_title.setText(f"{hit.path}  ·  {hit.commit.short_sha} {hit.commit.subject}")
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

        self.diff_title.setText(f"Every match at {hit.commit.short_sha} {hit.commit.subject}")
        self.diff.show_async(render, opts.python_pattern())
