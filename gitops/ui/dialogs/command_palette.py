from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QAction, QColor, QPainter
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QVBoxLayout

from gitops.ui.theme import colors

ACTION_ROLE = Qt.ItemDataRole.UserRole
SHORTCUT_ROLE = Qt.ItemDataRole.UserRole + 1


def matches(query: str, text: str) -> bool:
    """Every query character appears in order (so "nbr" finds "New branch")."""
    text = text.lower()
    pos = 0
    for ch in query.lower().replace(" ", ""):
        pos = text.find(ch, pos)
        if pos < 0:
            return False
        pos += 1
    return True


class CommandDelegate(QStyledItemDelegate):
    """Menu path in muted text, command name in full colour, shortcut as a key cap on the right."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 34)

    def paint(self, painter: QPainter, option, index) -> None:
        c = colors()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(2, 1, -2, -1)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c.accent_soft))
            painter.drawRoundedRect(r, 6, 6)
        right = r.right() - 10
        shortcut = index.data(SHORTCUT_ROLE)
        fm = option.fontMetrics
        if shortcut:
            w = fm.horizontalAdvance(shortcut) + 14
            cap = QRect(right - w, r.center().y() - 10, w, 20)
            painter.setPen(QColor(c.border_strong))
            painter.setBrush(QColor(c.field_bg))
            painter.drawRoundedRect(cap, 5, 5)
            painter.setPen(QColor(c.text_secondary))
            painter.drawText(cap, int(Qt.AlignmentFlag.AlignCenter), shortcut)
            right = cap.left() - 10
        path, _, name = index.data().rpartition(" › ")
        x = r.left() + 10
        width = right - x
        name = fm.elidedText(name, Qt.TextElideMode.ElideRight, width)
        painter.setPen(QColor(c.text))
        painter.drawText(QRect(x, r.top(), width, r.height()), int(Qt.AlignmentFlag.AlignVCenter), name)
        x += fm.horizontalAdvance(name) + 10
        if path and right - x > 30:
            painter.setPen(QColor(c.text_secondary))
            painter.drawText(QRect(x, r.top(), right - x, r.height()), int(Qt.AlignmentFlag.AlignVCenter),
                             fm.elidedText(path, Qt.TextElideMode.ElideLeft, right - x))
        painter.restore()


class CommandPalette(QDialog):
    """Ctrl+K: type to find any menu command, Enter to run it."""

    def __init__(self, actions: list[tuple[str, QAction]], parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("Palette")
        self.setMinimumWidth(520)
        self.actions = actions
        self.query = QLineEdit(placeholderText="Type a command…", objectName="SearchField")
        self.query.textChanged.connect(self._filter)
        self.query.returnPressed.connect(self._run_current)
        self.list = QListWidget()
        self.list.setItemDelegate(CommandDelegate(self.list))
        self.list.setMinimumHeight(320)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setUniformItemSizes(True)
        self.list.itemActivated.connect(lambda _it: self._run_current())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self.query)
        layout.addWidget(self.list)
        self.query.installEventFilter(self)
        self._filter("")

    def _filter(self, text: str) -> None:
        self.list.clear()
        for label, action in self.actions:
            if action.isEnabled() and matches(text, label):
                it = QListWidgetItem(label)
                it.setData(ACTION_ROLE, action)
                it.setData(SHORTCUT_ROLE, action.shortcut().toString(action.shortcut().SequenceFormat.NativeText))
                self.list.addItem(it)
        self.list.setCurrentRow(0)

    def eventFilter(self, obj, event) -> bool:
        # Arrow keys move through the list while typing stays in the search box.
        if obj is self.query and event.type() == event.Type.KeyPress and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = -1 if event.key() == Qt.Key.Key_Up else 1
            row = max(0, min(self.list.count() - 1, self.list.currentRow() + step))
            self.list.setCurrentRow(row)
            return True
        return super().eventFilter(obj, event)

    def _run_current(self) -> None:
        it = self.list.currentItem()
        if it is None:
            return
        action: QAction = it.data(ACTION_ROLE)
        self.accept()
        action.trigger()
