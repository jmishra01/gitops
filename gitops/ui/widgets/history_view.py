from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
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

from gitops.core.git_backend import GitBackend
from gitops.core.graph_layout import GraphRow, layout
from gitops.core.models import Commit
from gitops.services.task_runner import TaskRunner
from gitops.ui.theme import colors
from gitops.ui.widgets.commit_details import CommitDetails
from gitops.ui.widgets.common import item, make_table, paint_avatar, relative_time

LANE_COLORS = [QColor(c) for c in ("#0969da", "#1a7f37", "#bf3989", "#9a6700", "#8250df", "#cf222e", "#1b7c83")]
LANE_WIDTH = 14
GRAPH_ROLE = Qt.ItemDataRole.UserRole
REFS_ROLE = Qt.ItemDataRole.UserRole
COMMIT_ROLE = Qt.ItemDataRole.UserRole + 1


def paint_row_background(painter: QPainter, option: QStyleOptionViewItem) -> None:
    """Soft selection/hover drawn by us, so badges and text keep their colours (never white-on-blue)."""
    c = colors()
    r = option.rect
    if option.state & QStyle.StateFlag.State_Selected:
        painter.fillRect(r, QColor(c.accent_soft))
        painter.setPen(QPen(QColor(c.accent_soft_border), 1))
        painter.drawLine(r.topLeft(), r.topRight())
        painter.drawLine(r.bottomLeft(), r.bottomRight())
    elif option.state & QStyle.StateFlag.State_MouseOver:
        painter.fillRect(r, QColor(c.field_bg))
    else:
        painter.fillRect(r, QColor(c.surface))
        painter.setPen(QPen(QColor(c.field_bg), 1))
        painter.drawLine(r.bottomLeft(), r.bottomRight())


class TextDelegate(QStyledItemDelegate):
    """Author and time columns: secondary text on our own row background."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        paint_row_background(painter, option)
        c = colors()
        selected = option.state & QStyle.StateFlag.State_Selected
        painter.setPen(QColor(c.text if selected else c.text_secondary))
        align = Qt.AlignmentFlag.AlignVCenter | (
            Qt.AlignmentFlag.AlignRight if index.column() == 3 else Qt.AlignmentFlag.AlignLeft
        )
        rect = option.rect.adjusted(8, 0, -14, 0)
        text = option.fontMetrics.elidedText(str(index.data() or ""), Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, int(align), text)
        painter.restore()


class GraphDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        paint_row_background(painter, option)
        painter.restore()
        row: GraphRow | None = index.data(GRAPH_ROLE)
        if row is None:
            return
        r = option.rect
        top, bottom, mid = r.top(), r.bottom() + 1, r.center().y() + 0.5

        def x(lane: int) -> float:
            return r.left() + 8 + lane * LANE_WIDTH

        def color(lane: int) -> QColor:
            return LANE_COLORS[lane % len(LANE_COLORS)]

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for lane in row.passthrough:
            painter.setPen(QPen(color(lane), 2))
            painter.drawLine(QPointF(x(lane), top), QPointF(x(lane), bottom))
        for lane in row.incoming:
            painter.setPen(QPen(color(lane), 2))
            painter.drawLine(QPointF(x(lane), top), QPointF(x(row.column), mid))
        for lane in row.outgoing:
            painter.setPen(QPen(color(lane), 2))
            painter.drawLine(QPointF(x(row.column), mid), QPointF(x(lane), bottom))
        node = color(row.column)
        painter.setPen(QPen(node, 2))
        painter.setBrush(QColor(colors().surface) if len(row.outgoing) > 1 else node)
        painter.drawEllipse(QPointF(x(row.column), mid), 4, 4)
        painter.restore()


class SubjectDelegate(QStyledItemDelegate):
    """Avatar, then branch/tag pills, then the subject."""

    AVATAR = 24

    def __init__(self, parent=None):
        super().__init__(parent)
        self.remotes: set[str] = set()

    def _style(self, ref: str) -> tuple[str, str, str]:
        """(label, background, text) as theme attribute names."""
        if ref.startswith("tag: "):
            return ref[5:], "warning_soft", "warning_on_soft"
        if ref.startswith("HEAD -> "):
            return ref[8:], "accent_soft", "accent_on_soft"
        if ref == "HEAD":
            return "HEAD (detached)", "danger_soft", "danger_on_soft"
        if ref.split("/", 1)[0] in self.remotes:
            return ref, "field_bg", "text_secondary"
        return ref, "success_soft", "success_on_soft"

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        c = colors()
        painter.save()
        paint_row_background(painter, option)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        commit: Commit | None = index.data(COMMIT_ROLE)
        r = option.rect
        x = r.left() + 6
        if commit is not None:
            paint_avatar(painter, x, r.center().y() - self.AVATAR // 2 + 1, self.AVATAR, commit.author, commit.email)
            x += self.AVATAR + 10
        pill_font = QFont(option.font)
        pill_font.setPointSizeF(max(option.font.pointSizeF() - 1.5, 8))
        pill_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(pill_font)
        fm = painter.fontMetrics()
        for ref in commit.refs if commit else ():
            label, bg, fg = self._style(ref)
            w = fm.horizontalAdvance(label) + 14
            if x + w > r.right() - 80:
                break
            pill = QRect(x, r.center().y() - 10, w, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(getattr(c, bg)))
            painter.drawRoundedRect(pill, 6, 6)
            if ref.startswith("HEAD -> "):  # the checked-out branch gets an outline too
                painter.setPen(QPen(QColor(c.accent_soft_border), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(pill.adjusted(0, 0, -1, -1), 6, 6)
            painter.setPen(QColor(getattr(c, fg)))
            painter.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), label)
            x += w + 6
        subject_font = QFont(option.font)
        if option.state & QStyle.StateFlag.State_Selected:
            subject_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(subject_font)
        painter.setPen(QColor(c.text))
        text_rect = QRect(x + 2, r.top(), r.right() - x - 6, r.height())
        text = painter.fontMetrics().elidedText(str(index.data() or ""), Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        painter.restore()


class HistoryView(QWidget):
    reload_requested = Signal()
    load_more = Signal(int)  # skip: number of commits already shown
    filter_requested = Signal(str)  # empty string clears the filter
    commit_menu_requested = Signal(object, object)  # (Commit, global QPoint)

    PAGE_SIZE = 1000
    ROW_HEIGHT = 44

    def __init__(self, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.backend: GitBackend | None = None
        self.commits: list[Commit] = []
        self.has_more = False
        self.loading_more = False
        self.max_lanes = 1
        self.filtered = False

        # ---- header: title, branch scope, filter
        title = QLabel("History", objectName="Title")
        segmented = QFrame(objectName="Segmented")
        segmented.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        seg = QHBoxLayout(segmented)
        seg.setContentsMargins(2, 2, 2, 2)
        seg.setSpacing(2)
        self.current_only = QPushButton("Current branch")
        self.all_refs = QPushButton("All branches")  # checkable: isChecked() means all branches
        self.scope = QButtonGroup(self)
        for button in (self.current_only, self.all_refs):
            button.setCheckable(True)
            button.setProperty("variant", "segment")
            button.setMinimumWidth(button.fontMetrics().horizontalAdvance(button.text()) + 30)
            self.scope.addButton(button)
            seg.addWidget(button)
        self.current_only.setChecked(True)
        self.all_refs.toggled.connect(lambda _on: self.reload_requested.emit())
        self.filter_edit = QLineEdit(objectName="SearchField")
        self.filter_edit.setPlaceholderText("Filter: message text, author:name, or a SHA")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.returnPressed.connect(lambda: self.filter_requested.emit(self.filter_edit.text().strip()))
        self.filter_edit.textChanged.connect(lambda t: not t and self.filtered and self.filter_requested.emit(""))
        self.count_label = QLabel(objectName="Muted")
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 12, 16, 12)
        bar.setSpacing(10)
        bar.addWidget(title)
        bar.addSpacing(6)
        bar.addWidget(segmented)
        bar.addWidget(self.filter_edit, 1)
        bar.addWidget(self.count_label)

        # ---- commit list
        self.table, self.model = make_table(["Graph", "Message", "Author", "When"], 1)
        self.table.setObjectName("CommitTable")
        self.table.horizontalHeader().hide()
        self.table.setAlternatingRowColors(False)
        self.table.setMouseTracking(True)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.verticalHeader().setDefaultSectionSize(self.ROW_HEIGHT)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(2, header.ResizeMode.Fixed)
        header.setSectionResizeMode(3, header.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 150)
        self.table.setColumnWidth(3, 96)
        self.subject_delegate = SubjectDelegate(self.table)
        self.refs_delegate = self.subject_delegate  # older name, still used by the window
        text_delegate = TextDelegate(self.table)
        self.table.setItemDelegateForColumn(0, GraphDelegate(self.table))
        self.table.setItemDelegateForColumn(1, self.subject_delegate)
        self.table.setItemDelegateForColumn(2, text_delegate)
        self.table.setItemDelegateForColumn(3, text_delegate)
        self.table.selectionModel().currentRowChanged.connect(self._on_commit)
        self.table.verticalScrollBar().valueChanged.connect(self._maybe_load_more)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        list_panel = QFrame(objectName="Panel")
        list_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lp = QVBoxLayout(list_panel)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(0)
        lp.addLayout(bar)
        divider = QFrame(objectName="PanelDivider")
        divider.setFixedHeight(1)
        lp.addWidget(divider)
        lp.addWidget(self.table, 1)

        # ---- details
        self.details = CommitDetails(runner)

        split = QSplitter(objectName="history_list_split")
        split.addWidget(list_panel)
        split.addWidget(self.details)
        split.setStretchFactor(0, 1)
        split.setSizes([760, 520])
        split.setHandleWidth(1)
        layout_ = QVBoxLayout(self)
        layout_.setContentsMargins(0, 0, 0, 0)
        layout_.addWidget(split, 1)

    def set_backend(self, backend: GitBackend | None) -> None:
        self.backend = backend
        self.commits = []
        self.model.removeRows(0, self.model.rowCount())
        self.details.set_backend(backend)

    def _context_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if index.isValid() and index.row() < len(self.commits):
            self.table.selectRow(index.row())
            self.commit_menu_requested.emit(self.commits[index.row()], self.table.viewport().mapToGlobal(pos))

    def show_filtered(self, commits: list[Commit], query: str) -> None:
        self.populate(commits, list(self.subject_delegate.remotes))
        self.filtered = True
        self.has_more = False
        self.count_label.setText(f"{len(commits)} matching “{query}”")

    def populate(self, commits: list[Commit], remotes: list[str]) -> None:
        self.filtered = False
        selected_sha = (
            self.commits[self.table.currentIndex().row()].sha if self.table.currentIndex().isValid() else None
        )
        self.commits = []
        self.max_lanes = 1
        self.subject_delegate.remotes = set(remotes)
        self.model.removeRows(0, self.model.rowCount())
        self._append_rows(commits)
        target = next((n for n, c in enumerate(commits) if c.sha == selected_sha), 0)
        if commits:
            self.table.selectRow(target)
        else:
            self.details.clear()

    def append(self, commits: list[Commit]) -> None:
        """Add the next page below the rows already shown, keeping scroll and selection."""
        self.loading_more = False
        self._append_rows(commits)

    def select_sha(self, sha: str) -> bool:
        for n, c in enumerate(self.commits):
            if c.sha == sha:
                self.table.selectRow(n)
                self.table.scrollTo(self.model.index(n, 0))
                return True
        return False

    def _maybe_load_more(self, value: int) -> None:
        bar = self.table.verticalScrollBar()
        if self.has_more and not self.loading_more and value >= bar.maximum() - 20:
            self.loading_more = True
            self.load_more.emit(len(self.commits))

    def _append_rows(self, commits: list[Commit]) -> None:
        start = len(self.commits)
        self.commits = self.commits + commits
        self.has_more = len(commits) >= self.PAGE_SIZE
        # The layout is sequential, so earlier rows come out identical; only new rows are added.
        rows = layout(self.commits)[start:]
        for commit, graph in zip(commits, rows, strict=True):
            graph_item = item("")
            graph_item.setData(graph, GRAPH_ROLE)
            subject = item(commit.subject, tooltip=f"{commit.subject}\n{commit.short_sha}")
            subject.setData(commit.refs, REFS_ROLE)
            subject.setData(commit, COMMIT_ROLE)
            self.model.appendRow(
                [
                    graph_item,
                    subject,
                    item(commit.author, tooltip=commit.email),
                    item(relative_time(commit.date), tooltip=commit.date.strftime("%Y-%m-%d %H:%M")),
                ]
            )
        self.max_lanes = max([self.max_lanes] + [g.width for g in rows])
        self.table.horizontalHeader().setSectionResizeMode(0, self.table.horizontalHeader().ResizeMode.Fixed)
        self.table.setColumnWidth(0, max(20 + self.max_lanes * LANE_WIDTH, 44))
        more = "+" if self.has_more else ""
        self.count_label.setText(f"{len(self.commits)}{more} commits")

    def _on_commit(self, current, _previous) -> None:
        if current.isValid() and self.backend and current.row() < len(self.commits):
            self.details.show_commit(self.commits[current.row()])
