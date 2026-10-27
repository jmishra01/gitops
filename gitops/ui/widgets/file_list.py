"""The Changes file list: Staged / Not staged sections, a checkbox per file, status chips and +/- counts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from PySide6.QtCore import QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolTip

from gitops.ui.theme import colors

ROW_ROLE = Qt.ItemDataRole.UserRole + 10

# letter -> (chip text, chip tooltip, theme color pair)
CHIPS = {
    "M": ("M", "Modified", ("warning_soft", "warning_on_soft")),
    "A": ("A", "Added", ("success_soft", "success_on_soft")),
    "?": ("N", "New file", ("success_soft", "success_on_soft")),
    "D": ("D", "Deleted", ("danger_soft", "danger_on_soft")),
    "R": ("R", "Renamed", ("accent_soft", "accent_on_soft")),
    "C": ("C", "Copied", ("accent_soft", "accent_on_soft")),
    "T": ("T", "Type changed", ("warning_soft", "warning_on_soft")),
    "U": ("!", "Conflict", ("danger_soft", "danger_on_soft")),
}

CHECKBOX_X = 12
CHECKBOX_SIZE = 16
ROW_HEIGHT = 36
HEADER_HEIGHT = 32


@dataclass(frozen=True)
class FileRow:
    path: str
    staged: bool  # which section the row is in
    letter: str  # status letter for this side ("U" for conflicts, "?" for untracked)
    added: int | None = None
    removed: int | None = None
    note: str = ""  # e.g. "partly staged", "conflict"
    orig_path: str | None = None

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def folder(self) -> str:
        parent = str(PurePosixPath(self.path).parent)
        return "" if parent == "." else parent + "/"


@dataclass(frozen=True)
class SectionHeader:
    title: str
    count: int


class FileRowDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        row = index.data(ROW_ROLE)
        return QSize(option.rect.width(), HEADER_HEIGHT if isinstance(row, SectionHeader) else ROW_HEIGHT)

    @staticmethod
    def checkbox_rect(rect: QRect) -> QRect:
        return QRect(rect.left() + CHECKBOX_X, rect.center().y() - CHECKBOX_SIZE // 2 + 1, CHECKBOX_SIZE, CHECKBOX_SIZE)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row = index.data(ROW_ROLE)
        c = colors()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(6, 1, -6, -1)

        if isinstance(row, SectionHeader):
            font = QFont(option.font)
            font.setPointSizeF(max(font.pointSizeF() - 2, 8))
            font.setBold(True)
            font.setCapitalization(QFont.Capitalization.AllUppercase)
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
            painter.setFont(font)
            painter.setPen(QColor(c.text_secondary))
            text_rect = QRect(r.left() + 8, r.top() + 8, r.width() - 16, r.height() - 8)
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), row.title)
            title_w = painter.fontMetrics().horizontalAdvance(row.title)
            badge = QRect(text_rect.left() + title_w + 8, text_rect.center().y() - 8, 22, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c.field_bg))
            painter.drawRoundedRect(badge, 8, 8)
            painter.setPen(QColor(c.text))
            painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), str(row.count))
            painter.restore()
            return

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            painter.setPen(QPen(QColor(c.accent_soft_border), 1))
            painter.setBrush(QColor(c.accent_soft))
            painter.drawRoundedRect(r, 8, 8)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c.field_bg))
            painter.drawRoundedRect(r, 8, 8)

        # Checkbox: checked means "staged" (rows in the Staged section).
        box = self.checkbox_rect(option.rect)
        painter.setPen(QPen(QColor(c.primary if row.staged else c.border_strong), 1.5))
        painter.setBrush(QColor(c.primary) if row.staged else QColor(c.surface))
        painter.drawRoundedRect(box.adjusted(0, 0, -1, -1), 4, 4)
        if row.staged:
            painter.setPen(
                QPen(QColor("#FFFFFF"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            )
            painter.drawPolyline(
                [
                    QPoint(box.left() + 4, box.top() + 8),
                    QPoint(box.left() + 7, box.top() + 11),
                    QPoint(box.left() + 12, box.top() + 5),
                ]
            )

        # Status chip
        chip_text, _tip, (bg, fg) = CHIPS.get(row.letter, (row.letter, "", ("field_bg", "text")))
        chip = QRect(box.right() + 10, option.rect.center().y() - 9, 18, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(getattr(c, bg)))
        painter.drawRoundedRect(chip, 4, 4)
        chip_font = QFont(option.font)
        chip_font.setBold(True)
        chip_font.setPointSizeF(max(chip_font.pointSizeF() - 2, 8))
        painter.setFont(chip_font)
        painter.setPen(QColor(getattr(c, fg)))
        painter.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), chip_text)

        # Right side: counts or note
        mono = QFont(option.font)
        mono.setFamilies(["JetBrains Mono", "Menlo", "Consolas", "monospace"])
        mono.setPointSizeF(max(option.font.pointSizeF() - 2, 8))
        painter.setFont(mono)
        right = r.right() - 8
        if row.note:
            note_font = QFont(option.font)
            note_font.setPointSizeF(max(option.font.pointSizeF() - 2, 8))
            painter.setFont(note_font)
            painter.setPen(QColor(c.accent_on_soft if not row.letter == "U" else c.danger_on_soft))
            w = painter.fontMetrics().horizontalAdvance(row.note)
            painter.drawText(QRect(right - w, r.top(), w, r.height()), int(Qt.AlignmentFlag.AlignVCenter), row.note)
            right -= w + 8
        elif row.added is not None or row.removed is not None:
            removed = f"−{row.removed or 0}"
            added = f"+{row.added or 0}"
            fm = painter.fontMetrics()
            painter.setPen(QColor(c.del_fg))
            w = fm.horizontalAdvance(removed)
            painter.drawText(QRect(right - w, r.top(), w, r.height()), int(Qt.AlignmentFlag.AlignVCenter), removed)
            right -= w + 6
            painter.setPen(QColor(c.add_fg))
            w = fm.horizontalAdvance(added)
            painter.drawText(QRect(right - w, r.top(), w, r.height()), int(Qt.AlignmentFlag.AlignVCenter), added)
            right -= w + 8

        # Name and folder, elided to fit
        name_font = QFont(option.font)
        name_font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
        painter.setFont(name_font)
        left = chip.right() + 10
        available = max(right - left, 10)
        fm = painter.fontMetrics()
        label = (
            row.name
            if row.letter not in "RC" or not row.orig_path
            else f"{PurePosixPath(row.orig_path).name} → {row.name}"
        )
        name = fm.elidedText(label, Qt.TextElideMode.ElideMiddle, available)
        painter.setPen(QColor(c.text))
        painter.drawText(QRect(left, r.top(), available, r.height()), int(Qt.AlignmentFlag.AlignVCenter), name)
        name_w = fm.horizontalAdvance(name)
        if row.folder and name_w + 20 < available:
            painter.setFont(option.font)
            fm2 = painter.fontMetrics()
            folder = fm2.elidedText(row.folder, Qt.TextElideMode.ElideLeft, available - name_w - 6)
            painter.setPen(QColor(c.text_secondary))
            painter.drawText(
                QRect(left + name_w + 6, r.top(), available - name_w - 6, r.height()),
                int(Qt.AlignmentFlag.AlignVCenter),
                folder,
            )
        painter.restore()

    def helpEvent(self, event, view, option, index) -> bool:
        row = index.data(ROW_ROLE)
        if isinstance(row, FileRow):
            tip = CHIPS.get(row.letter, ("", row.letter, None))[1]
            QToolTip.showText(event.globalPos(), f"{row.path}\n{tip}" + (f" · {row.note}" if row.note else ""), view)
            return True
        return super().helpEvent(event, view, option, index)


class ChangeFileList(QListView):
    """Emits intents; the Changes view decides what git does."""

    file_selected = Signal(object)  # FileRow
    toggle_requested = Signal(object)  # FileRow: stage it, or unstage if in Staged
    activated_row = Signal(object)  # double-click / Enter
    menu_requested = Signal(list, object)  # [FileRow], global QPoint

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_ = QStandardItemModel(self)
        self.setModel(self.model_)
        self.setItemDelegate(FileRowDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMouseTracking(True)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setUniformItemSizes(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.selectionModel().currentChanged.connect(self._current_changed)
        self.doubleClicked.connect(lambda idx: self._emit_row(self.activated_row, idx))
        self.setStyleSheet("QListView { background: transparent; outline: 0; }")

    # ------------------------------------------------------------------ content

    def set_rows(self, staged: list[FileRow], unstaged: list[FileRow]) -> None:
        self.selectionModel().blockSignals(True)
        self.model_.clear()
        for title, rows in (("Staged", staged), ("Not staged", unstaged)):
            if not rows:
                continue
            header = QStandardItem()
            header.setData(SectionHeader(title, len(rows)), ROW_ROLE)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.model_.appendRow(header)
            for row in rows:
                it = QStandardItem()
                it.setData(row, ROW_ROLE)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.model_.appendRow(it)
        self.selectionModel().blockSignals(False)

    def rows(self) -> list[FileRow]:
        out = []
        for r in range(self.model_.rowCount()):
            row = self.model_.item(r).data(ROW_ROLE)
            if isinstance(row, FileRow):
                out.append(row)
        return out

    def paths(self, staged: bool) -> list[str]:
        return [r.path for r in self.rows() if r.staged == staged]

    def select(self, path: str, staged: bool) -> bool:
        for r in range(self.model_.rowCount()):
            row = self.model_.item(r).data(ROW_ROLE)
            if isinstance(row, FileRow) and row.path == path and row.staged == staged:
                self.setCurrentIndex(self.model_.index(r, 0))
                return True
        return False

    def selected_rows(self) -> list[FileRow]:
        rows = [i.data(ROW_ROLE) for i in self.selectionModel().selectedIndexes()]
        return [r for r in rows if isinstance(r, FileRow)]

    # ------------------------------------------------------------------ interaction

    def _emit_row(self, signal, index: QModelIndex) -> None:
        row = index.data(ROW_ROLE)
        if isinstance(row, FileRow):
            signal.emit(row)

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self._emit_row(self.file_selected, current)

    def mousePressEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        row = index.data(ROW_ROLE) if index.isValid() else None
        if isinstance(row, FileRow) and event.button() == Qt.MouseButton.LeftButton:
            hit = FileRowDelegate.checkbox_rect(self.visualRect(index)).adjusted(-6, -8, 6, 8)
            if hit.contains(event.position().toPoint()):
                self.toggle_requested.emit(row)
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space:
            row = self.currentIndex().data(ROW_ROLE)
            if isinstance(row, FileRow):
                self.toggle_requested.emit(row)
                return
        super().keyPressEvent(event)

    def _menu(self, pos) -> None:
        index = self.indexAt(pos)
        if not index.isValid() or not isinstance(index.data(ROW_ROLE), FileRow):
            return
        if not self.selectionModel().isSelected(index):
            self.setCurrentIndex(index)
        rows = self.selected_rows() or [index.data(ROW_ROLE)]
        # A menu acts on one section at a time, the one that was clicked.
        clicked = index.data(ROW_ROLE)
        rows = [r for r in rows if r.staged == clicked.staged]
        self.menu_requested.emit(rows, self.viewport().mapToGlobal(pos))
