"""Small stroke icons drawn with QPainter, so they follow the theme and need no image files.

Each icon is drawn on a 24x24 grid, the same geometry as the design mockups.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def _path(draw) -> QPainterPath:
    p = QPainterPath()
    draw(p)
    return p


def _branch(p: QPainterPath) -> None:
    p.addEllipse(QPointF(6, 6), 2, 2)
    p.addEllipse(QPointF(6, 18), 2, 2)
    p.addEllipse(QPointF(18, 8), 2, 2)
    p.moveTo(6, 8)
    p.lineTo(6, 16)
    p.moveTo(18, 10)
    p.cubicTo(18, 14, 12, 14, 7, 16)


def _folder(p: QPainterPath) -> None:
    p.moveTo(3, 7)
    p.lineTo(9, 7)
    p.lineTo(11, 9)
    p.lineTo(21, 9)
    p.lineTo(21, 19)
    p.lineTo(3, 19)
    p.closeSubpath()


def _search(p: QPainterPath) -> None:
    p.addEllipse(QPointF(11, 11), 7, 7)
    p.moveTo(16.5, 16.5)
    p.lineTo(20, 20)


def _sync(p: QPainterPath) -> None:
    p.moveTo(4, 12)
    p.arcTo(QRectF(4, 4, 16, 16), 180, -130)
    p.moveTo(20, 12)
    p.arcTo(QRectF(4, 4, 16, 16), 0, -130)
    p.moveTo(18, 3)
    p.lineTo(18, 7)
    p.lineTo(14, 7)
    p.moveTo(6, 21)
    p.lineTo(6, 17)
    p.lineTo(10, 17)


def _undo(p: QPainterPath) -> None:
    p.moveTo(9, 14)
    p.lineTo(4, 9)
    p.lineTo(9, 4)
    p.moveTo(4, 9)
    p.lineTo(14, 9)
    p.arcTo(QRectF(8, 9, 12, 12), 90, -180)
    p.lineTo(11, 21)


def _terminal(p: QPainterPath) -> None:
    p.addRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
    p.moveTo(7, 9)
    p.lineTo(10, 12)
    p.lineTo(7, 15)
    p.moveTo(13, 15)
    p.lineTo(17, 15)


def _settings(p: QPainterPath) -> None:
    p.addEllipse(QPointF(12, 12), 3, 3)
    for a, b in (
        ((12, 2), (12, 5)),
        ((12, 19), (12, 22)),
        ((2, 12), (5, 12)),
        ((19, 12), (22, 12)),
        ((4.9, 4.9), (7, 7)),
        ((17, 17), (19.1, 19.1)),
        ((4.9, 19.1), (7, 17)),
        ((17, 7), (19.1, 4.9)),
    ):
        p.moveTo(*a)
        p.lineTo(*b)


def _changes(p: QPainterPath) -> None:
    p.addRoundedRect(QRectF(3, 3, 18, 18), 3, 3)
    p.moveTo(12, 7)
    p.lineTo(12, 17)
    p.moveTo(7, 12)
    p.lineTo(17, 12)


def _history(p: QPainterPath) -> None:
    p.addEllipse(QPointF(12, 12), 9, 9)
    p.moveTo(12, 7)
    p.lineTo(12, 12)
    p.lineTo(15, 14)


def _file(p: QPainterPath) -> None:
    p.moveTo(6, 3)
    p.lineTo(14, 3)
    p.lineTo(19, 8)
    p.lineTo(19, 21)
    p.lineTo(6, 21)
    p.closeSubpath()
    p.moveTo(14, 3)
    p.lineTo(14, 8)
    p.lineTo(19, 8)


def _blame(p: QPainterPath) -> None:
    p.addEllipse(QPointF(7, 7), 3, 3)
    p.moveTo(2, 18)
    p.cubicTo(2, 13, 12, 13, 12, 18)
    p.moveTo(15, 6)
    p.lineTo(22, 6)
    p.moveTo(15, 11)
    p.lineTo(22, 11)
    p.moveTo(15, 16)
    p.lineTo(22, 16)


def _grid(p: QPainterPath) -> None:
    for x, y in ((3, 3), (14, 3), (3, 14), (14, 14)):
        p.addRoundedRect(QRectF(x, y, 7, 7), 1.5, 1.5)


def _chevron(p: QPainterPath) -> None:
    p.moveTo(6, 9)
    p.lineTo(12, 15)
    p.lineTo(18, 9)


def _logo(p: QPainterPath) -> None:
    p.addEllipse(QPointF(6, 6), 2.5, 2.5)
    p.addEllipse(QPointF(6, 18), 2.5, 2.5)
    p.addEllipse(QPointF(18, 9), 2.5, 2.5)
    p.moveTo(6, 8.5)
    p.lineTo(6, 15.5)
    p.moveTo(18, 11.5)
    p.cubicTo(18, 15.5, 12, 14.5, 7, 16.5)


def _stash(p: QPainterPath) -> None:
    p.addRoundedRect(QRectF(3, 4, 18, 5), 1.5, 1.5)
    p.moveTo(5, 9)
    p.lineTo(5, 20)
    p.lineTo(19, 20)
    p.lineTo(19, 9)
    p.moveTo(10, 13)
    p.lineTo(14, 13)


def _tag(p: QPainterPath) -> None:
    p.moveTo(3, 3)
    p.lineTo(11, 3)
    p.lineTo(21, 13)
    p.lineTo(13, 21)
    p.lineTo(3, 11)
    p.closeSubpath()
    p.addEllipse(QPointF(7.5, 7.5), 1.5, 1.5)


def _cloud(p: QPainterPath) -> None:
    p.moveTo(7, 18)
    p.cubicTo(3, 18, 3, 12, 7, 12)
    p.cubicTo(7, 6, 16, 6, 16, 11)
    p.cubicTo(21, 11, 21, 18, 17, 18)
    p.closeSubpath()


SHAPES = {
    "branch": _branch,
    "folder": _folder,
    "search": _search,
    "sync": _sync,
    "undo": _undo,
    "terminal": _terminal,
    "settings": _settings,
    "changes": _changes,
    "history": _history,
    "file": _file,
    "blame": _blame,
    "grid": _grid,
    "chevron": _chevron,
    "logo": _logo,
    "stash": _stash,
    "tag": _tag,
    "cloud": _cloud,
}


def pixmap(name: str, color: str, size: int = 20, width: float = 2.0, ratio: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24, size / 24)
    painter.setPen(
        QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_path(SHAPES[name]))
    painter.end()
    return pm


def icon(name: str, color: str, size: int = 20, active_color: str | None = None) -> QIcon:
    """An icon whose checked/selected state can use a different color (nav rail)."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, active_color or color, size), QIcon.Mode.Normal, QIcon.State.On)
    return ic
