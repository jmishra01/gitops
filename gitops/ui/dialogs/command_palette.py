from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

ACTION_ROLE = Qt.ItemDataRole.UserRole


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


class CommandPalette(QDialog):
    """Ctrl+K: type to find any menu command, Enter to run it."""

    def __init__(self, actions: list[tuple[str, QAction]], parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setMinimumWidth(520)
        self.actions = actions
        self.query = QLineEdit(placeholderText="Type a command…")
        self.query.textChanged.connect(self._filter)
        self.query.returnPressed.connect(self._run_current)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _it: self._run_current())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.query)
        layout.addWidget(self.list)
        self.query.installEventFilter(self)
        self._filter("")

    def _filter(self, text: str) -> None:
        self.list.clear()
        for label, action in self.actions:
            if action.isEnabled() and matches(text, label):
                shortcut = action.shortcut().toString()
                it = QListWidgetItem(f"{label}    {shortcut}" if shortcut else label)
                it.setData(ACTION_ROLE, action)
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
