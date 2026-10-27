from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QBoxLayout, QHeaderView, QLabel, QTableView, QWidget

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


def set_tone(label: QLabel, tone: str | None) -> None:
    """Colour a status label from the theme ("danger", "warning", "success"); follows theme switches."""
    label.setProperty("tone", tone or "")
    label.style().unpolish(label)
    label.style().polish(label)


def dialog_heading(dialog: QWidget, layout: QBoxLayout, title: str, intro: str = "") -> QLabel | None:
    """Opt a dialog into the workbench look and put a title (and optional intro) at the top of its layout."""
    dialog.setObjectName("Dialog")
    layout.setContentsMargins(20, 18, 20, 16)
    layout.setSpacing(10)
    layout.insertWidget(0, QLabel(title, objectName="Title"))
    if not intro:
        return None
    label = QLabel(intro, objectName="Muted")
    label.setWordWrap(True)
    layout.insertWidget(1, label)
    return label


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


# Avatar colours: mid-tone, all with white text at 4.5:1 or better.
AVATAR_COLORS = ("#6D4BD8", "#0F766E", "#B4540A", "#2F5BEA", "#B42318", "#7A5A00", "#1F7A3A", "#9C2E8A")


def initials(name: str) -> str:
    parts = [p for p in name.replace(".", " ").replace("_", " ").split() if p]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


def avatar_color(key: str) -> str:
    # Stable across runs (unlike hash()), so a person keeps their colour.
    return AVATAR_COLORS[sum(key.encode()) % len(AVATAR_COLORS)]


def paint_avatar(painter: QPainter, x: int, y: int, size: int, name: str, key: str | None = None) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(avatar_color(key or name)))
    painter.drawEllipse(x, y, size, size)
    font = QFont(painter.font())
    font.setPixelSize(max(int(size * 0.42), 8))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#FFFFFF"))
    painter.drawText(x, y, size, size, int(Qt.AlignmentFlag.AlignCenter), initials(name))
    painter.restore()


def avatar_pixmap(name: str, size: int = 22, key: str | None = None, ratio: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    paint_avatar(painter, 0, 0, size, name, key)
    painter.end()
    return pm
