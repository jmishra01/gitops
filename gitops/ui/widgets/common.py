from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from gitops.core.models import ChangeKind

KIND_COLORS = {
    ChangeKind.CREATED: "#1a7f37",
    ChangeKind.ADDED: "#1a7f37",
    ChangeKind.MODIFIED: "#9a6700",
    ChangeKind.CHANGED: "#9a6700",
    ChangeKind.RENAMED: "#0969da",
    ChangeKind.COPIED: "#0969da",
    ChangeKind.DELETED: "#cf222e",
    ChangeKind.REMOVED: "#cf222e",
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
    it.setForeground(QBrush(QColor(KIND_COLORS.get(kind, "#57606a"))))
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
