from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from gitops.core.models import ChangeKind
from gitops.ui.theme import colors

# Semantic color names, resolved against the active theme when an item is built.
KIND_COLORS = {
    ChangeKind.CREATED: "success",
    ChangeKind.ADDED: "success",
    ChangeKind.MODIFIED: "warning",
    ChangeKind.CHANGED: "warning",
    ChangeKind.RENAMED: "accent",
    ChangeKind.COPIED: "accent",
    ChangeKind.DELETED: "danger",
    ChangeKind.REMOVED: "danger",
}


def relative_time(ts: datetime) -> str:
    secs = int((datetime.now(ts.tzinfo) - ts).total_seconds())
    for unit, size in (("y", 31_536_000), ("mo", 2_592_000), ("d", 86_400), ("h", 3_600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit} ago"
    return "just now"


def item(text: str, data=None, tooltip: str | None = None) -> QStandardItem:
    it = QStandardItem(text)
    it.setEditable(False)
    if data is not None:
        it.setData(data, Qt.ItemDataRole.UserRole)
    if tooltip:
        it.setToolTip(tooltip)
    return it


def kind_item(kind: ChangeKind) -> QStandardItem:
    it = item(kind.value)
    it.setForeground(QBrush(QColor(getattr(colors(), KIND_COLORS.get(kind, "muted")))))
    font = it.font()
    font.setBold(True)
    it.setFont(font)
    return it


def make_table(headers: list[str], stretch_column: int) -> tuple[QTableView, QStandardItemModel]:
    model = QStandardItemModel(0, len(headers))
    model.setHorizontalHeaderLabels(headers)
    view = QTableView()
    view.setModel(model)
    view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setShowGrid(False)
    view.setAlternatingRowColors(True)
    view.verticalHeader().hide()
    view.verticalHeader().setDefaultSectionSize(24)
    header = view.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(stretch_column, QHeaderView.ResizeMode.Stretch)
    return view, model
