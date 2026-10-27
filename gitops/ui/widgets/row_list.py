"""A sectioned list of two-line rows (title, subtitle, status chip), shared by the Branches and Stashes pages."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from gitops.ui import icons
from gitops.ui.theme import colors

ROW_ROLE = Qt.ItemDataRole.UserRole + 40
ROW_HEIGHT = 54
HEADER_HEIGHT = 34

# chip kind -> (background, text) theme attributes
CHIP_COLORS = {
    "success": ("success_soft", "success_on_soft"),
    "warning": ("warning_soft", "warning_on_soft"),
    "danger": ("danger_soft", "danger_on_soft"),
    "accent": ("accent_soft", "accent_on_soft"),
    "muted": ("field_bg", "text_secondary"),
}


@dataclass(frozen=True)
class Row:
    key: str
    kind: str  # what the owning page uses to pick actions: local, remote, tag, worktree, submodule, stash
    title: str
    subtitle: str = ""
    chip: str = ""
    chip_kind: str = "muted"
    icon: str = ""
    current: bool = False  # drawn with a dot and a bold title
    data: object = field(default=None, compare=False)


@dataclass(frozen=True)
class Header:
    title: str
    count: int


class RowDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), HEADER_HEIGHT if isinstance(index.data(ROW_ROLE), Header) else ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        item = index.data(ROW_ROLE)
        c = colors()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(8, 1, -8, -1)

        if isinstance(item, Header):
            font = QFont(option.font)
            font.setBold(True)
            font.setPointSizeF(max(font.pointSizeF() - 2, 8))
            font.setCapitalization(QFont.Capitalization.AllUppercase)
            painter.setFont(font)
            painter.setPen(QColor(c.text_secondary))
            text_rect = QRect(r.left() + 8, r.top() + 10, r.width() - 16, r.height() - 10)
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), item.title)
            w = painter.fontMetrics().horizontalAdvance(item.title)
            badge = QRect(text_rect.left() + w + 8, text_rect.center().y() - 8, 24, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c.field_bg))
            painter.drawRoundedRect(badge, 8, 8)
            painter.setPen(QColor(c.text))
            painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), str(item.count))
            painter.restore()
            return

        row: Row = item
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QColor(c.accent_soft_border))
            painter.setBrush(QColor(c.accent_soft))
            painter.drawRoundedRect(r, 10, 10)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c.field_bg))
            painter.drawRoundedRect(r, 10, 10)

        x = r.left() + 12
        if row.icon:
            painter.drawPixmap(x, r.top() + 10, icons.pixmap(row.icon, c.primary if row.current else c.text_secondary, 16))
            x += 26

        right = r.right() - 12
        if row.chip:
            bg, fg = CHIP_COLORS.get(row.chip_kind, CHIP_COLORS["muted"])
            chip_font = QFont(option.font)
            chip_font.setPointSizeF(max(option.font.pointSizeF() - 1.5, 8))
            chip_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(chip_font)
            w = painter.fontMetrics().horizontalAdvance(row.chip) + 16
            chip = QRect(right - w, r.top() + 9, w, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(getattr(c, bg)))
            painter.drawRoundedRect(chip, 10, 10)
            painter.setPen(QColor(getattr(c, fg)))
            painter.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), row.chip)
            right = chip.left() - 10

        title_font = QFont(option.font)
        title_font.setWeight(QFont.Weight.DemiBold if row.current else QFont.Weight.Medium)
        painter.setFont(title_font)
        width = max(right - x, 20)
        title = painter.fontMetrics().elidedText(("● " if row.current else "") + row.title, Qt.TextElideMode.ElideMiddle, width)
        painter.setPen(QColor(c.text))
        painter.drawText(QRect(x, r.top() + 8, width, 22), int(Qt.AlignmentFlag.AlignVCenter), title)
        if row.subtitle:
            painter.setFont(option.font)
            painter.setPen(QColor(c.text_secondary))
            sub_w = r.right() - 12 - x
            subtitle = painter.fontMetrics().elidedText(row.subtitle, Qt.TextElideMode.ElideRight, sub_w)
            painter.drawText(QRect(x, r.top() + 29, sub_w, 18), int(Qt.AlignmentFlag.AlignVCenter), subtitle)
        painter.restore()


class RowList(QListView):
    current_row = Signal(object)  # Row or None
    activated_row = Signal(object)  # double-click / Enter
    menu_requested = Signal(object, object)  # (Row, global QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = QStandardItemModel(self)
        self.setModel(self.model_)
        self.setItemDelegate(RowDelegate(self))
        self.setMouseTracking(True)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QListView { background: transparent; outline: 0; }")
        self.selectionModel().currentChanged.connect(lambda cur, _prev: self.current_row.emit(self._row(cur)))
        self.doubleClicked.connect(lambda idx: (row := self._row(idx)) and self.activated_row.emit(row))

    @staticmethod
    def _row(index: QModelIndex) -> Row | None:
        item = index.data(ROW_ROLE) if index.isValid() else None
        return item if isinstance(item, Row) else None

    def set_sections(self, sections: list[tuple[str, list[Row]]], keep: str | None = None) -> None:
        """Replace the content; empty sections are skipped. Re-selects `keep` (a row key) if still there."""
        self.selectionModel().blockSignals(True)
        self.model_.clear()
        for title, rows in sections:
            if not rows:
                continue
            header = QStandardItem()
            header.setData(Header(title, len(rows)), ROW_ROLE)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.model_.appendRow(header)
            for row in rows:
                it = QStandardItem()
                it.setData(row, ROW_ROLE)
                it.setToolTip(row.title + (f"\n{row.subtitle}" if row.subtitle else ""))
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.model_.appendRow(it)
        self.selectionModel().blockSignals(False)
        if keep is None or not self.select(keep):
            self.current_row.emit(None)

    def rows(self) -> list[Row]:
        out = []
        for r in range(self.model_.rowCount()):
            item = self.model_.item(r).data(ROW_ROLE)
            if isinstance(item, Row):
                out.append(item)
        return out

    def select(self, key: str) -> bool:
        for r in range(self.model_.rowCount()):
            item = self.model_.item(r).data(ROW_ROLE)
            if isinstance(item, Row) and item.key == key:
                self.setCurrentIndex(self.model_.index(r, 0))
                self.current_row.emit(item)
                return True
        return False

    def current(self) -> Row | None:
        return self._row(self.currentIndex())

    def contextMenuEvent(self, event) -> None:
        index = self.indexAt(event.pos())
        row = self._row(index)
        if row:
            self.setCurrentIndex(index)
            self.menu_requested.emit(row, event.globalPos())

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (row := self.current()):
            self.activated_row.emit(row)
            return
        super().keyPressEvent(event)

    def point_of(self, key: str) -> QPoint | None:
        for r in range(self.model_.rowCount()):
            item = self.model_.item(r).data(ROW_ROLE)
            if isinstance(item, Row) and item.key == key:
                return self.viewport().mapToGlobal(self.visualRect(self.model_.index(r, 0)).center())
        return None
