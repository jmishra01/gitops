"""Timeline scrubber for one file: a dot per version placed by date, with A and B markers to compare."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from gitops.ui.theme import colors

DOT_Y = 22
MARGIN = 16


@dataclass(frozen=True)
class Mark:
    when: datetime
    kind: str  # "working", "created", "renamed", "deleted", "modified"
    tip: str


class TimelineScrubber(QWidget):
    """Versions are given newest first (index 0 is usually the working copy) and drawn oldest-left."""

    a_changed = Signal(int)
    b_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.marks: list[Mark] = []
        self.a: int | None = None
        self.b: int | None = None
        self._dragging = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("File timeline: click to choose version A, Shift-click for B, arrow keys to step")

    def sizeHint(self) -> QSize:
        return QSize(600, 66)

    def minimumSizeHint(self) -> QSize:
        return QSize(200, 66)

    # ------------------------------------------------------------------ data

    def set_marks(self, marks: list[Mark], a: int | None, b: int | None) -> None:
        self.marks = marks
        self.a = a
        self.b = b
        self.update()

    def set_a(self, index: int, emit: bool = True) -> None:
        if 0 <= index < len(self.marks) and index != self.a:
            self.a = index
            self.update()
            if emit:
                self.a_changed.emit(index)

    def set_b(self, index: int, emit: bool = True) -> None:
        if 0 <= index < len(self.marks) and index != self.b:
            self.b = index
            self.update()
            if emit:
                self.b_changed.emit(index)

    # ------------------------------------------------------------------ geometry

    def _span(self) -> tuple[float, float]:
        stamps = [m.when.timestamp() for m in self.marks]
        lo, hi = min(stamps), max(stamps)
        return lo, (hi if hi > lo else lo + 1)

    def x_of(self, index: int) -> float:
        lo, hi = self._span()
        usable = self.width() - 2 * MARGIN
        return MARGIN + usable * (self.marks[index].when.timestamp() - lo) / (hi - lo)

    def index_at(self, x: float) -> int | None:
        if not self.marks:
            return None
        return min(range(len(self.marks)), key=lambda i: abs(self.x_of(i) - x))

    # ------------------------------------------------------------------ painting

    def paintEvent(self, _event) -> None:
        if not self.marks:
            return
        c = colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right = MARGIN, self.width() - MARGIN
        p.setPen(QPen(QColor(c.border_strong), 2))
        p.drawLine(QPointF(left, DOT_Y), QPointF(right, DOT_Y))
        if self.a is not None and self.b is not None:
            xa, xb = sorted((self.x_of(self.a), self.x_of(self.b)))
            p.setPen(QPen(QColor(c.accent_soft_border), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(xa, DOT_Y), QPointF(xb, DOT_Y))

        kind_colors = {"created": c.add_fg, "deleted": c.del_fg, "renamed": c.hunk, "working": c.text}
        ring = QColor(c.surface)
        for i, mark in enumerate(self.marks):
            if i in (self.a, self.b):
                continue
            x = self.x_of(i)
            color = QColor(kind_colors.get(mark.kind, c.text_secondary))
            p.setPen(QPen(ring, 3))
            p.setBrush(color)
            if mark.kind == "renamed":
                path = QPainterPath()
                path.moveTo(x, DOT_Y - 8)
                path.lineTo(x + 8, DOT_Y)
                path.lineTo(x, DOT_Y + 8)
                path.lineTo(x - 8, DOT_Y)
                path.closeSubpath()
                p.drawPath(path)
            else:
                r = 7 if mark.kind in ("created", "deleted") else 5.5
                p.drawEllipse(QPointF(x, DOT_Y), r, r)

        letter_font = QFont(self.font())
        letter_font.setBold(True)
        letter_font.setPixelSize(11)
        p.setFont(letter_font)
        for index, letter, fill, text_color in ((self.a, "A", c.primary, "#FFFFFF"), (self.b, "B", c.text, c.surface)):
            if index is None:
                continue
            x = self.x_of(index)
            p.setPen(QPen(ring, 3))
            p.setBrush(QColor(fill))
            p.drawEllipse(QPointF(x, DOT_Y), 11, 11)
            p.setPen(QColor(text_color))
            p.drawText(QRectF(x - 11, DOT_Y - 11, 22, 22), int(Qt.AlignmentFlag.AlignCenter), letter)

        # Date labels: oldest, a few evenly spaced points, and the newest.
        small = QFont(self.font())
        small.setPixelSize(11)
        p.setFont(small)
        p.setPen(QColor(c.text_secondary))
        lo, hi = self._span()
        ticks = 5
        for t in range(ticks + 1):
            frac = t / ticks
            when = datetime.fromtimestamp(lo + (hi - lo) * frac).astimezone()
            label = (
                when.strftime("%b %d")
                if t in (0, ticks)
                else when.strftime("%b %Y" if hi - lo > 300 * 86400 else "%b %d")
            )
            if t == ticks and self.marks[0].kind == "working":
                label = "Now"
            x = left + (right - left) * frac
            w = p.fontMetrics().horizontalAdvance(label)
            tx = min(max(x - w / 2, 0), self.width() - w)
            p.drawText(QPointF(tx, DOT_Y + 32), label)
        p.end()

    # ------------------------------------------------------------------ interaction

    def mousePressEvent(self, event) -> None:
        index = self.index_at(event.position().x())
        if index is None:
            return
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier or event.button() == Qt.MouseButton.RightButton:
            self.set_b(index)
        else:
            self.set_a(index)
            self._dragging = True

    def mouseMoveEvent(self, event) -> None:
        index = self.index_at(event.position().x())
        if index is None:
            return
        if self._dragging:
            self.set_a(index)
        if abs(self.x_of(index) - event.position().x()) < 12:
            QToolTip.showText(event.globalPosition().toPoint(), self.marks[index].tip, self)

    def mouseReleaseEvent(self, _event) -> None:
        self._dragging = False

    def keyPressEvent(self, event) -> None:
        if self.a is None:
            return super().keyPressEvent(event)
        # Newest is index 0 and drawn on the right, so Right moves toward index 0.
        if event.key() == Qt.Key.Key_Right:
            self.set_a(max(self.a - 1, 0))
        elif event.key() == Qt.Key.Key_Left:
            self.set_a(min(self.a + 1, len(self.marks) - 1))
        else:
            super().keyPressEvent(event)
