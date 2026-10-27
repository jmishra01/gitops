"""Diff shown as hunk cards: line numbers, a checkbox on every change, and a button per hunk.

Selection is kept as patch line indices (the same indices gitops.core.patch
uses), so whatever the user ticks can be staged, unstaged or discarded exactly.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from gitops.core.encoding import display_text
from gitops.core.patch import FilePatch, Hunk
from gitops.ui.theme import colors

LINE_HEIGHT = 22
CHECK_W, OLD_W, NEW_W, SIGN_W = 32, 46, 46, 18
TEXT_X = CHECK_W + OLD_W + NEW_W + SIGN_W


def mono_font(base: QFont | None = None) -> QFont:
    font = QFont(base) if base else QFont()
    font.setFamilies(["JetBrains Mono", "SF Mono", "Menlo", "Consolas", "DejaVu Sans Mono", "monospace"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(12)
    return font


@dataclass(frozen=True)
class DiffLine:
    index: int  # line index in the patch text
    kind: str  # " ", "+", "-", or "\\" for "No newline at end of file"
    old_no: int | None
    new_no: int | None
    text: str  # display-safe text

    @property
    def is_change(self) -> bool:
        return self.kind in ("+", "-")


def hunk_lines(hunk: Hunk) -> list[DiffLine]:
    old_no, new_no = hunk.old_start, hunk.new_start
    out: list[DiffLine] = []
    for index, raw in hunk.lines:
        kind = raw[:1] or " "
        text = display_text(raw[1:]).replace("\t", "    ")
        if kind == "+":
            out.append(DiffLine(index, kind, None, new_no, text))
            new_no += 1
        elif kind == "-":
            out.append(DiffLine(index, kind, old_no, None, text))
            old_no += 1
        elif kind == "\\":
            out.append(DiffLine(index, kind, None, None, raw[1:].strip()))
        else:
            out.append(DiffLine(index, " ", old_no, new_no, text))
            old_no += 1
            new_no += 1
    return out


class HunkLines(QWidget):
    """Custom-painted lines of one hunk (only the visible rows are drawn)."""

    def __init__(self, view: HunkDiffView, lines: list[DiffLine]):
        super().__init__()
        self.view = view
        self.lines = lines
        self.font_ = mono_font(self.font())
        fm = QFontMetrics(self.font_)
        widest = max((fm.horizontalAdvance(line.text) for line in lines), default=0)
        self.content_width = TEXT_X + widest + 24
        self.setMinimumWidth(self.content_width)
        self.setFixedHeight(max(len(lines), 1) * LINE_HEIGHT)
        self._drag_state: bool | None = None
        self.setMouseTracking(False)

    def sizeHint(self) -> QSize:
        return QSize(self.content_width, len(self.lines) * LINE_HEIGHT)

    def _row_at(self, y: float) -> int | None:
        row = int(y // LINE_HEIGHT)
        return row if 0 <= row < len(self.lines) else None

    def paintEvent(self, event) -> None:
        c = colors()
        painter = QPainter(self)
        painter.setFont(self.font_)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        first = max(event.rect().top() // LINE_HEIGHT, 0)
        last = min(event.rect().bottom() // LINE_HEIGHT + 1, len(self.lines))
        width = self.width()
        selected = self.view.selected
        for row in range(first, last):
            line = self.lines[row]
            top = row * LINE_HEIGHT
            rect = QRect(0, top, width, LINE_HEIGHT)
            if line.kind == "+":
                painter.fillRect(rect, QColor(c.add_bg))
            elif line.kind == "-":
                painter.fillRect(rect, QColor(c.del_bg))
            is_selected = line.index in selected
            if is_selected:
                tint = QColor(c.primary)
                tint.setAlpha(18)
                painter.fillRect(rect, tint)

            if line.is_change and self.view.selectable:
                box = QRect(CHECK_W // 2 - 7, top + LINE_HEIGHT // 2 - 7, 14, 14)
                painter.setPen(QPen(QColor(c.primary if is_selected else c.border_strong), 1.4))
                painter.setBrush(QColor(c.primary) if is_selected else QColor(c.surface))
                painter.drawRoundedRect(box, 3, 3)
                if is_selected:
                    painter.setPen(QPen(QColor("#FFFFFF"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                    painter.drawPolyline(
                        [
                            QPoint(box.left() + 3, box.top() + 7),
                            QPoint(box.left() + 6, box.top() + 10),
                            QPoint(box.left() + 11, box.top() + 4),
                        ]
                    )

            painter.setPen(QColor(c.text_secondary))
            align_right = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if line.old_no is not None:
                painter.drawText(QRect(CHECK_W, top, OLD_W - 10, LINE_HEIGHT), align_right, str(line.old_no))
            if line.new_no is not None:
                painter.drawText(QRect(CHECK_W + OLD_W, top, NEW_W - 10, LINE_HEIGHT), align_right, str(line.new_no))
            left_mid = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if line.kind in ("+", "-"):
                painter.setPen(QColor(c.add_fg if line.kind == "+" else c.del_fg))
                painter.drawText(
                    QRect(CHECK_W + OLD_W + NEW_W, top, SIGN_W, LINE_HEIGHT), left_mid, "+" if line.kind == "+" else "−"
                )
            if line.kind == "\\":
                painter.setPen(QColor(c.text_secondary))
                italic = QFont(self.font_)
                italic.setItalic(True)
                painter.setFont(italic)
                painter.drawText(QRect(TEXT_X, top, width - TEXT_X, LINE_HEIGHT), left_mid, line.text)
                painter.setFont(self.font_)
            else:
                painter.setPen(QColor(c.text))
                painter.drawText(QRect(TEXT_X, top, width - TEXT_X, LINE_HEIGHT), left_mid, line.text)
        painter.end()

    # Click a change line to tick it, Shift-click for a range, drag to paint over several.
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.view.selectable:
            return super().mousePressEvent(event)
        row = self._row_at(event.position().y())
        if row is None or not self.lines[row].is_change:
            return
        line = self.lines[row]
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and self.view.anchor is not None:
            self.view.select_range(self.view.anchor, line.index, True)
        else:
            new_state = line.index not in self.view.selected
            self.view.set_line(line.index, new_state)
            self._drag_state = new_state
            self.view.anchor = line.index

    def mouseMoveEvent(self, event) -> None:
        if self._drag_state is None:
            return
        row = self._row_at(event.position().y())
        if row is not None and self.lines[row].is_change:
            self.view.set_line(self.lines[row].index, self._drag_state)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_state = None
        super().mouseReleaseEvent(event)


class HunkCard(QFrame):
    def __init__(self, view: HunkDiffView, hunk_no: int, hunk: Hunk, action_label: str | None):
        super().__init__(objectName="HunkCard")
        self.lines = HunkLines(view, hunk_lines(hunk))
        header = QWidget(objectName="HunkHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        old_count = sum(1 for _i, raw in hunk.lines if raw[:1] in (" ", "-", ""))
        new_count = sum(1 for _i, raw in hunk.lines if raw[:1] in (" ", "+", ""))
        title = QLabel(
            f"@@ -{hunk.old_start},{old_count} +{hunk.new_start},{new_count} @@", objectName="HunkHeaderText"
        )
        title.setFont(mono_font(self.font()))
        context = QLabel(hunk.section.strip(), objectName="Muted")
        context.setFont(mono_font(self.font()))
        self.action = QPushButton(action_label or "")
        self.action.setProperty("variant", "secondary")
        self.action.setVisible(action_label is not None)  # read-only diffs (history) have no hunk actions
        self.action.clicked.connect(lambda: view.hunk_action.emit(hunk_no))
        row = QHBoxLayout(header)
        row.setContentsMargins(14, 6, 8, 6)
        row.setSpacing(12)
        row.addWidget(title)
        row.addWidget(context, 1)
        row.addWidget(self.action)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 6)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.lines)


class HunkDiffView(QScrollArea):
    hunk_action = Signal(int)  # hunk number: stage/unstage it
    selection_changed = Signal(int)  # number of selected lines

    def __init__(self, parent=None):
        super().__init__(parent, objectName="DiffScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.canvas = QWidget(objectName="DiffCanvas")
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.column = QVBoxLayout(self.canvas)
        self.column.setContentsMargins(20, 16, 20, 88)  # room at the bottom for the selection bar
        self.column.setSpacing(16)
        self.setWidget(self.canvas)
        self.patch: FilePatch | None = None
        self.selectable = True
        self.selected: set[int] = set()
        self.anchor: int | None = None
        self.cards: list[HunkCard] = []

    def show_patch(self, patch: FilePatch, action_label: str | None, selectable: bool = True) -> None:
        self.patch = patch
        self.selectable = selectable
        self.selected = set()
        self.anchor = None
        while self.column.count():
            item = self.column.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards = [HunkCard(self, n, hunk, action_label) for n, hunk in enumerate(patch.hunks)]
        for card in self.cards:
            self.column.addWidget(card)
        self.column.addStretch(1)
        self.selection_changed.emit(0)

    # ------------------------------------------------------------------ selection

    def _change_order(self) -> list[int]:
        return [line.index for card in self.cards for line in card.lines.lines if line.is_change]

    def set_line(self, index: int, state: bool) -> None:
        if (index in self.selected) == state:
            return
        if state:
            self.selected.add(index)
        else:
            self.selected.discard(index)
        self._repaint()

    def select_range(self, a: int, b: int, state: bool) -> None:
        lo, hi = min(a, b), max(a, b)
        for index in self._change_order():
            if lo <= index <= hi:
                (self.selected.add if state else self.selected.discard)(index)
        self._repaint()

    def clear_selection(self) -> None:
        if self.selected:
            self.selected.clear()
            self._repaint()

    def find_line(self, raw: str) -> int | None:
        """Patch index of the first line whose raw text (with its +/-/space prefix) equals `raw`."""
        for card in self.cards:
            for line in card.lines.lines:
                prefix = {"+": "+", "-": "-", " ": " "}.get(line.kind, "")
                if prefix + line.text == raw:
                    return line.index
        return None

    def plain_text(self) -> str:
        """The diff as text (for tests and copying)."""
        out = []
        for card, hunk in zip(self.cards, self.patch.hunks if self.patch else [], strict=False):
            out.append(f"@@ {hunk.section.strip()}")
            for line in card.lines.lines:
                out.append(("−" if line.kind == "-" else line.kind) + line.text if line.kind != "\\" else line.text)
        return "\n".join(out)

    def _repaint(self) -> None:
        for card in self.cards:
            card.lines.update()
        self.selection_changed.emit(len(self.selected))

    def apply_theme(self) -> None:
        self._repaint()
