from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
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
from gitops.ui.widgets.common import item, make_table, relative_time
from gitops.ui.widgets.diff_view import DiffView

LANE_COLORS = [QColor(c) for c in ("#0969da", "#1a7f37", "#bf3989", "#9a6700", "#8250df", "#cf222e", "#1b7c83")]
LANE_WIDTH = 14
GRAPH_ROLE = Qt.ItemDataRole.UserRole
REFS_ROLE = Qt.ItemDataRole.UserRole


class GraphDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
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
        painter.setBrush(option.palette.base() if len(row.outgoing) > 1 else node)
        painter.drawEllipse(QPointF(x(row.column), mid), 4, 4)
        painter.restore()


class RefsDelegate(QStyledItemDelegate):
    """Commit subject preceded by branch/tag badges."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.remotes: set[str] = set()

    def _style(self, ref: str) -> tuple[str, QColor]:
        if ref.startswith("tag: "):
            return ref[5:], QColor("#9a6700")
        if ref.startswith("HEAD -> "):
            return ref[8:], QColor("#1a7f37")
        if ref == "HEAD":
            return "HEAD", QColor("#cf222e")
        if ref.split("/", 1)[0] in self.remotes:
            return ref, QColor("#0969da")
        return ref, QColor("#1a7f37")

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ""
        opt.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        refs = index.data(REFS_ROLE) or ()
        r = option.rect
        fm = opt.fontMetrics
        x = r.left() + 4
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for ref in refs:
            label, color = self._style(ref)
            w = fm.horizontalAdvance(label) + 10
            if x + w > r.right() - 40:
                break
            badge = QRect(x, r.top() + 3, w, r.height() - 6)
            fill = QColor(color)
            fill.setAlpha(36)
            painter.setPen(QPen(color, 1))
            painter.setBrush(fill)
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(color)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
            x += w + 4
        selected = opt.state & QStyle.StateFlag.State_Selected
        painter.setPen(opt.palette.highlightedText().color() if selected else opt.palette.text().color())
        text_rect = QRect(x + 2, r.top(), r.right() - x - 4, r.height())
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            fm.elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width()),
        )
        painter.restore()


class HistoryView(QWidget):
    reload_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.backend: GitBackend | None = None
        self.commits: list[Commit] = []

        self.all_refs = QCheckBox("All branches")
        self.all_refs.toggled.connect(lambda _on: self.reload_requested.emit())
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: palette(placeholder-text);")
        bar = QHBoxLayout()
        bar.addWidget(self.all_refs)
        bar.addStretch(1)
        bar.addWidget(self.count_label)

        self.table, self.model = make_table(["Graph", "Message", "Author", "When", "Commit"], 1)
        self.refs_delegate = RefsDelegate(self.table)
        self.table.setItemDelegateForColumn(0, GraphDelegate(self.table))
        self.table.setItemDelegateForColumn(1, self.refs_delegate)
        self.table.selectionModel().currentRowChanged.connect(self._on_commit)
        self.diff = DiffView()

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.diff)
        split.setSizes([340, 400])
        layout_ = QVBoxLayout(self)
        layout_.addLayout(bar)
        layout_.addWidget(split, 1)

    def set_backend(self, backend: GitBackend | None) -> None:
        self.backend = backend
        self.commits = []
        self.model.removeRows(0, self.model.rowCount())
        self.diff.clear()

    def populate(self, commits: list[Commit], remotes: list[str]) -> None:
        selected_sha = self.commits[self.table.currentIndex().row()].sha if self.table.currentIndex().isValid() else None
        self.commits = commits
        self.refs_delegate.remotes = set(remotes)
        rows = layout(commits)
        self.model.removeRows(0, self.model.rowCount())
        for commit, graph in zip(commits, rows):
            graph_item = item("")
            graph_item.setData(graph, GRAPH_ROLE)
            subject = item(commit.subject, tooltip=commit.subject)
            subject.setData(commit.refs, REFS_ROLE)
            self.model.appendRow([
                graph_item,
                subject,
                item(commit.author, tooltip=commit.email),
                item(relative_time(commit.date), tooltip=commit.date.strftime("%Y-%m-%d %H:%M")),
                item(commit.short_sha, tooltip=commit.sha),
            ])
        lanes = max((g.width for g in rows), default=1)
        self.table.horizontalHeader().setSectionResizeMode(0, self.table.horizontalHeader().ResizeMode.Fixed)
        self.table.setColumnWidth(0, max(16 + lanes * LANE_WIDTH, 52))
        self.count_label.setText(f"{len(commits)} commits")
        target = next((n for n, c in enumerate(commits) if c.sha == selected_sha), 0)
        if commits:
            self.table.selectRow(target)

    def _on_commit(self, current, _previous) -> None:
        if current.isValid() and self.backend and current.row() < len(self.commits):
            self.diff.show_diff(self.backend.commit_diff(self.commits[current.row()].sha))
