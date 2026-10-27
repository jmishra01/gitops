"""Conflict resolver: the result in the centre, ours and theirs on either side, one decision per conflict.

Works across every conflicted file of a merge, rebase, cherry-pick or stash, with progress
at the top. Shared text and picked sides keep their original bytes and line endings; only
the conflicts you decide (or type) change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gitops.core.compare import is_binary
from gitops.core.conflicts import (
    BASE_MARK,
    OURS_MARK,
    SPLIT_MARK,
    THEIRS_MARK,
    Choice,
    Decision,
    Segment,
    eol_of,
    render_resolution,
    split_segments,
)
from gitops.core.encoding import pick_encoding
from gitops.core.git_backend import GitBackend, GitError
from gitops.ui.theme import colors
from gitops.ui.widgets.hunk_view import mono_font

LINE_H = 22
COLUMN_STRETCH = (4, 5, 4)  # ours : result : theirs
FOLD_OVER = 8  # unchanged runs longer than this are folded
CHOICE_LABELS = {
    Choice.OURS: "ours",
    Choice.THEIRS: "theirs",
    Choice.BOTH: "ours, then theirs",
    Choice.BASE: "the common ancestor",
}


def _fmt(bg: str, fg: str | None = None, bold: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setBackground(QColor(bg))
    if fg:
        fmt.setForeground(QColor(fg))
    if bold:
        fmt.setFontWeight(700)
    return fmt


class ConflictHighlighter(QSyntaxHighlighter):
    """Tints the ours / base / theirs sections between conflict markers (whole-file edit mode)."""

    def __init__(self, document):
        super().__init__(document)
        self.load_theme()

    def load_theme(self) -> None:
        c = colors()
        self.MARKER = _fmt(c.marker_bg, c.muted, bold=True)
        # Block states: 0 outside, 1 ours, 2 base, 3 theirs
        self.SECTION = {1: _fmt(c.ours_bg), 2: _fmt(c.base_bg), 3: _fmt(c.theirs_bg)}

    def highlightBlock(self, text: str) -> None:
        state = max(self.previousBlockState(), 0)
        if text.startswith(OURS_MARK):
            self.setFormat(0, len(text), self.MARKER)
            self.setCurrentBlockState(1)
        elif text.startswith(BASE_MARK) and state == 1:
            self.setFormat(0, len(text), self.MARKER)
            self.setCurrentBlockState(2)
        elif text.startswith(SPLIT_MARK) and state in (1, 2):
            self.setFormat(0, len(text), self.MARKER)
            self.setCurrentBlockState(3)
        elif text.startswith(THEIRS_MARK) and state == 3:
            self.setFormat(0, len(text), self.MARKER)
            self.setCurrentBlockState(0)
        else:
            if state in self.SECTION:
                self.setFormat(0, max(len(text), 1), self.SECTION[state])
            self.setCurrentBlockState(state)


# ---------------------------------------------------------------------- per-file state


@dataclass
class FileState:
    path: str
    base: bytes | None
    ours: bytes | None
    theirs: bytes | None
    working: bytes | None
    encoding: str = "utf-8"
    text: str = ""
    eol: str = "\n"
    segments: list[Segment] = field(default_factory=list)
    decisions: dict[int, Decision] = field(default_factory=dict)
    resolved: bool = False  # written and staged

    @classmethod
    def load(cls, backend: GitBackend, path: str) -> FileState:
        base, ours, theirs = backend.conflict_versions(path)
        target = backend.root / path
        working = target.read_bytes() if target.is_file() else None
        state = cls(path, base, ours, theirs, working)
        # Decode every version the same way and save back with it, so untouched bytes survive exactly.
        state.encoding = pick_encoding(base, ours, theirs, working)
        if not state.special:
            state.set_text((working or b"").decode(state.encoding))
        return state

    @property
    def binary(self) -> bool:
        return any(d is not None and is_binary(d) for d in (self.base, self.ours, self.theirs, self.working))

    @property
    def special(self) -> str | None:
        """Why this file can't be resolved conflict by conflict, if it can't."""
        if self.binary:
            return "binary"
        if self.ours is None or self.theirs is None:
            return "deleted"
        return None

    def set_text(self, text: str) -> None:
        self.text = text
        self.eol = eol_of(text)
        self.segments = split_segments(text)
        self.decisions = {}

    @property
    def conflict_count(self) -> int:
        return sum(1 for s in self.segments if s.is_conflict)

    @property
    def undecided(self) -> list[int]:
        return [n for n in range(self.conflict_count) if n not in self.decisions]

    def result_text(self) -> str:
        return render_resolution(self.segments, self.decisions, self.eol)


def _discard(widget: QWidget) -> None:
    """Remove a widget now: hidden at once, deleted later (so no stale frame paints in between)."""
    widget.hide()
    widget.deleteLater()


def _display(line: str) -> str:
    return line.rstrip("\r\n").replace("\t", "    ")


# ---------------------------------------------------------------------- painted code


class CodeCell(QWidget):
    """Read-only lines of code, painted; optional line numbers; transparent unless given a fill."""

    def __init__(self, lines: list[str], first_no: int | None = None, muted: bool = False):
        super().__init__()
        self.lines = [_display(line) for line in lines]
        self.first_no = first_no
        self.muted = muted
        self.font_ = mono_font(self.font())
        self.setFixedHeight(len(self.lines) * LINE_H)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(200, len(self.lines) * LINE_H)

    def paintEvent(self, _event) -> None:
        c = colors()
        p = QPainter(self)
        p.setFont(self.font_)
        gutter = 44 if self.first_no is not None else 12
        for i, text in enumerate(self.lines):
            top = i * LINE_H
            if self.first_no is not None:
                p.setPen(QColor(c.text_secondary))
                p.drawText(
                    0,
                    top,
                    gutter - 10,
                    LINE_H,
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    str(self.first_no + i),
                )
            p.setPen(QColor(c.text_secondary if self.muted else c.text))
            p.drawText(
                gutter,
                top,
                max(self.width() - gutter - 6, 0),
                LINE_H,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                text,
            )
        p.end()


def _row(cells: tuple[QWidget, QWidget, QWidget]) -> QWidget:
    """Three cells side by side with the fixed ours:result:theirs proportions."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    for cell, stretch in zip(cells, COLUMN_STRETCH, strict=True):
        cell.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(cell, stretch)
    return row


def _column_cell(name: str, child: QWidget | None = None, margins=(8, 4, 8, 4)) -> QFrame:
    cell = QFrame(objectName=name)
    cell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QVBoxLayout(cell)
    layout.setContentsMargins(*margins)
    layout.setSpacing(0)
    if child is not None:
        layout.addWidget(child)
    layout.addStretch(1)
    return cell


class AutoHeightEdit(QPlainTextEdit):
    """Editable code that grows with its content instead of scrolling inside the page."""

    def __init__(self, text: str):
        super().__init__()
        self.setFont(mono_font(self.font()))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QPlainTextEdit { background: transparent; border: none; }")
        self.setPlainText(text)
        self.textChanged.connect(self._fit)
        self._fit()

    def _fit(self) -> None:
        lines = max(self.document().blockCount(), 1)
        self.setFixedHeight(lines * (self.fontMetrics().lineSpacing() + 1) + 14)


# ---------------------------------------------------------------------- the dialog


class ConflictResolver(QDialog):
    """Resolve every conflicted file of the current operation.

    `outcome` tells the caller what to do afterwards: "commit" (finish the merge or
    continue the rebase), "abort", or None (just closed).
    """

    file_resolved = Signal(str)

    def __init__(self, backend: GitBackend, start_path: str | None = None, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.outcome: str | None = None
        self.operation = backend.operation_in_progress()
        paths = backend.conflicted_paths()
        if start_path and start_path not in paths:
            paths.insert(0, start_path)
        self.paths: list[str] = paths
        self.states: dict[str, FileState] = {}
        self.current: FileState | None = None
        self.focus = 0  # conflict number the toolbar navigates from
        self.show_base = False
        self.setWindowTitle("Resolve conflicts")
        self.resize(1400, 880)
        self.setObjectName("ChangesPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # ---- header: what's going on, progress, files
        self.title = QLabel(objectName="Title")
        self.subtitle = QLabel(objectName="Muted")
        self.abort_btn = QPushButton("Abort…")
        self.abort_btn.setProperty("variant", "danger")
        self.abort_btn.clicked.connect(self._abort)
        self.finish_btn = QPushButton()
        self.finish_btn.setProperty("variant", "primary")
        self.finish_btn.clicked.connect(self._finish)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(8)
        head_top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head_top.addLayout(titles, 1)
        head_top.addWidget(self.abort_btn)
        head_top.addWidget(self.finish_btn)
        header = QFrame(objectName="DiffHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hl = QVBoxLayout(header)
        hl.setContentsMargins(20, 14, 20, 12)
        hl.setSpacing(10)
        hl.addLayout(head_top)
        hl.addWidget(self.progress)
        hl.addLayout(self.chips)

        # ---- toolbar: navigate conflicts, bulk picks, whole-file editing
        self.counter = QLabel()
        self.counter.setStyleSheet("font-weight: 600;")
        self.prev_btn = QPushButton("↑")
        self.prev_btn.setAccessibleName("Previous conflict")
        self.next_btn = QPushButton("↓")
        self.next_btn.setAccessibleName("Next conflict")
        for b in (self.prev_btn, self.next_btn):
            b.setProperty("variant", "secondary")
            b.setFixedWidth(34)
        self.prev_btn.clicked.connect(lambda: self._jump(-1))
        self.next_btn.clicked.connect(lambda: self._jump(1))
        self.base_box = QCheckBox("Show common ancestor")
        self.base_box.toggled.connect(self._toggle_base)
        self.all_ours_btn = QPushButton("Take all of ours")
        self.all_theirs_btn = QPushButton("Take all of theirs")
        self.raw_btn = QPushButton("Edit whole file")
        self.raw_btn.setCheckable(True)
        for b in (self.all_ours_btn, self.all_theirs_btn, self.raw_btn):
            b.setProperty("variant", "secondary")
        self.all_ours_btn.clicked.connect(lambda: self.decide_all(Choice.OURS))
        self.all_theirs_btn.clicked.connect(lambda: self.decide_all(Choice.THEIRS))
        self.raw_btn.toggled.connect(self._toggle_raw)
        toolbar = QFrame(objectName="DiffHeader")
        toolbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tl = QHBoxLayout(toolbar)
        tl.setContentsMargins(20, 8, 20, 8)
        tl.setSpacing(8)
        for w in (self.counter, self.prev_btn, self.next_btn):
            tl.addWidget(w)
        tl.addStretch(1)
        for w in (self.base_box, self.all_ours_btn, self.all_theirs_btn, self.raw_btn):
            tl.addWidget(w)

        # ---- column headings
        self.ours_head = QLabel()
        self.result_head = QLabel()
        self.theirs_head = QLabel()
        for label in (self.ours_head, self.result_head, self.theirs_head):
            label.setTextFormat(Qt.TextFormat.RichText)
        heads = _row(
            (
                _column_cell("ColOurs", self.ours_head, (14, 10, 14, 10)),
                _column_cell("ColResult", self.result_head, (14, 10, 14, 10)),
                _column_cell("ColTheirs", self.theirs_head, (14, 10, 14, 10)),
            )
        )

        # ---- the three columns
        self.rows_host = QWidget(objectName="ColumnsHost")
        self.rows_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.rows = QVBoxLayout(self.rows_host)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.rows_host)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.conflict_rows: list[QWidget] = []

        self.raw = QPlainTextEdit()
        self.raw.setFont(mono_font(self.raw.font()))
        self.raw.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.raw_highlighter = ConflictHighlighter(self.raw.document())
        self.special = QWidget()
        self.special_layout = QVBoxLayout(self.special)
        self.special_layout.setContentsMargins(40, 40, 40, 40)
        self.done_page = QLabel(objectName="EmptyState")
        self.done_page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.done_page.setWordWrap(True)
        self.body = QStackedWidget()
        for w in (self.scroll, self.raw, self.special, self.done_page):
            self.body.addWidget(w)
        columns = QWidget()
        cl = QVBoxLayout(columns)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(heads)
        cl.addWidget(self.body, 1)
        self.heads = heads

        # ---- footer
        self.legend = QLabel()
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.encoding_note = QLabel(objectName="Muted")
        self.mark_btn = QPushButton()
        self.mark_btn.clicked.connect(self.mark_resolved)
        footer = QFrame(objectName="DiffHeader")
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(20, 10, 20, 10)
        fl.addWidget(self.legend)
        fl.addStretch(1)
        fl.addWidget(self.encoding_note)
        fl.addWidget(self.mark_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(toolbar)
        layout.addWidget(columns, 1)
        layout.addWidget(footer)

        self.apply_theme()
        first = start_path if start_path in self.paths else (self.paths[0] if self.paths else None)
        if first:
            self.open_file(first)
        else:
            self._show_done()

    # ------------------------------------------------------------------ theme

    def apply_theme(self) -> None:
        c = colors()
        self.setStyleSheet(
            f"#ColOurs, #ColTheirs {{ background: {c.surface}; }}"
            f" #ColResult {{ background: {c.surface_alt}; border-left: 1px solid {c.border}; border-right: 1px solid {c.border}; }}"
            f" #ColumnsHost {{ background: {c.surface}; }}"
            f" QProgressBar {{ background: {c.border}; border: none; border-radius: 3px; }}"
            f" QProgressBar::chunk {{ background: {c.add_fg}; border-radius: 3px; }}"
        )
        swatch = "<span style='background:{bg}; border:1px solid {bd};'>&nbsp;&nbsp;&nbsp;</span>"
        self.legend.setText(
            f"{swatch.format(bg=c.ours_bg, bd=c.ours_border)} <span style='color:{c.text_secondary}'>Only in ours</span>"
            f"&nbsp;&nbsp;&nbsp;{swatch.format(bg=c.theirs_bg, bd=c.theirs_border)} <span style='color:{c.text_secondary}'>Only in theirs</span>"
            f"&nbsp;&nbsp;&nbsp;{swatch.format(bg=c.decide_bg, bd=c.decide_border)} <span style='color:{c.text_secondary}'>Needs a decision</span>"
        )
        self.raw_highlighter.load_theme()
        self.raw_highlighter.rehighlight()
        if self.current:
            self._render()

    # ------------------------------------------------------------------ files

    def state(self, path: str) -> FileState:
        if path not in self.states:
            self.states[path] = FileState.load(self.backend, path)
        return self.states[path]

    def open_file(self, path: str) -> None:
        self.current = self.state(path)
        self.focus = 0
        self.raw_btn.blockSignals(True)
        self.raw_btn.setChecked(False)
        self.raw_btn.blockSignals(False)
        self._render()

    def _resolved_count(self) -> int:
        return sum(1 for p in self.paths if p in self.states and self.states[p].resolved)

    def _render_header(self) -> None:
        c = colors()
        names = {
            "merge": "Merging",
            "rebase": "Rewriting history (rebase)",
            "cherry-pick": "Cherry-picking",
            "revert": "Reverting",
            None: "Applying a stash",
        }
        title = names.get(self.operation, "Resolving conflicts")
        if self.operation == "merge":
            subject = self.backend.merge_message().split("\n", 1)[0]
            title = f"{title} · {subject}" if subject else title
        self.title.setText(title)
        done, total = self._resolved_count(), len(self.paths)
        self.subtitle.setText(
            f"{done} of {total} file{'s' if total != 1 else ''} resolved · nothing is committed until you finish"
        )
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.finish_btn.setText(
            {"merge": "Commit merge", None: "Done"}.get(self.operation, f"Continue {self.operation}")
        )
        self.abort_btn.setVisible(self.operation is not None)

        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget():
                _discard(item.widget())
        for path in self.paths:
            st = self.states.get(path)
            current = self.current is not None and self.current.path == path
            if st and st.resolved:
                text, style = f"✓  {path}", f"background:{c.success_soft}; color:{c.success_on_soft};"
            elif current:
                left = len(st.undecided) if st and not st.special else None
                note = (
                    f" · {left} of {st.conflict_count} conflicts left" if left is not None and st.conflict_count else ""
                )
                text, style = (
                    f"●  {path}{note}",
                    f"background:{c.surface}; color:{c.text}; border: 1.5px solid {c.primary}; font-weight:600;",
                )
            else:
                text, style = f"●  {path}", f"background:{c.field_bg}; color:{c.text};"
            chip = QPushButton(text)
            chip.setStyleSheet(f"QPushButton {{ {style} border-radius: 8px; padding: 4px 10px; }}")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _=False, p=path: self.open_file(p))
            self.chips.addWidget(chip)
        self.chips.addStretch(1)

    def _render(self) -> None:
        self._render_header()
        st = self.current
        if st is None:
            return
        c = colors()
        ours_name = self.backend.current_branch() or "HEAD"
        block = next((s.block for s in st.segments if s.is_conflict), None)
        theirs_name = block.theirs_label if block and block.theirs_label else "incoming"
        self.ours_head.setText(
            f"<span style='color:{c.primary}'>●</span> <b>Ours</b> <span style='color:{c.text_secondary}'>· {ours_name}</span>"
        )
        self.result_head.setText(
            f"<b>Result</b> <span style='color:{c.text_secondary}'>· what gets saved · type in resolved parts</span>"
        )
        self.theirs_head.setText(
            f"<span style='color:{c.add_fg}'>●</span> <b>Theirs</b> <span style='color:{c.text_secondary}'>· {theirs_name}</span>"
        )
        self.encoding_note.setText(
            "Not UTF-8: shown as Latin-1, untouched bytes are kept exactly." if st.encoding != "utf-8" else ""
        )
        if st.resolved:
            self._show_message(f"{st.path} is resolved and staged.")
        elif st.special:
            self._render_special(st)
        elif self.raw_btn.isChecked():
            self.heads.hide()
            self.body.setCurrentWidget(self.raw)
        else:
            self._render_rows(st)
        self._update_controls()

    def _show_message(self, text: str) -> None:
        self.done_page.setText(text)
        self.body.setCurrentWidget(self.done_page)
        self.heads.hide()

    def _show_done(self) -> None:
        self._render_header()
        self._show_message("Every conflicted file is resolved. Finish to complete the operation.")
        self._update_controls()

    # ------------------------------------------------------------------ rows

    def _clear_rows(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                _discard(item.widget())
        self.conflict_rows = []

    def _render_rows(self, st: FileState) -> None:
        self.heads.show()
        scroll = self.scroll.verticalScrollBar().value()
        self._clear_rows()
        result_no = 1
        n = 0
        for seg in st.segments:
            if not seg.is_conflict:
                self.rows.addWidget(self._context_row(seg.lines, result_no))
                result_no += len(seg.lines)
                continue
            row, produced = self._conflict_row(st, n, seg)
            self.rows.addWidget(row)
            self.conflict_rows.append(row)
            result_no += produced
            n += 1
        self.rows.addStretch(1)
        self.body.setCurrentWidget(self.scroll)
        self.scroll.verticalScrollBar().setValue(scroll)

    def _plain_row(self, lines: list[str], first_no: int) -> QWidget:
        return _row(
            (
                _column_cell("ColOurs", CodeCell(lines, muted=True)),
                _column_cell("ColResult", CodeCell(lines, first_no)),
                _column_cell("ColTheirs", CodeCell(lines, muted=True)),
            )
        )

    def _context_row(self, lines: list[str], first_no: int) -> QWidget:
        if len(lines) <= FOLD_OVER:
            return self._plain_row(lines, first_no)
        # Long unchanged runs: show the edges, fold the middle.
        hidden = len(lines) - 6
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._plain_row(lines[:3], first_no))
        fold = QPushButton(f"⋯  {hidden} unchanged lines")
        fold.setProperty("variant", "secondary")
        fold.clicked.connect(lambda: self._expand(wrap, lines, first_no))
        v.addWidget(
            _row((_column_cell("ColOurs"), _column_cell("ColResult", fold, (14, 2, 14, 2)), _column_cell("ColTheirs")))
        )
        v.addWidget(self._plain_row(lines[-3:], first_no + len(lines) - 3))
        return wrap

    def _expand(self, wrap: QWidget, lines: list[str], first_no: int) -> None:
        self.rows.insertWidget(self.rows.indexOf(wrap), self._plain_row(lines, first_no))
        _discard(wrap)

    def _side_card(self, lines: list[str], ours: bool, n: int) -> QFrame:
        c = colors()
        card = QFrame()
        bg, border = (c.ours_bg, c.ours_border) if ours else (c.theirs_bg, c.theirs_border)
        card.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }} QLabel {{ border: none; }}"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 4, 0, 6)
        v.setSpacing(4)
        v.addWidget(CodeCell(lines) if lines else QLabel("   (nothing on this side)", objectName="Muted"))
        button = QPushButton("Use ours  →" if ours else "←  Use theirs")
        fill = c.primary if ours else c.success_fill
        button.setStyleSheet(
            f"QPushButton {{ background: {fill}; color: #FFFFFF; border: none; border-radius: 6px; padding: 4px 10px; font-weight: 600; }}"
        )
        button.clicked.connect(lambda: self.decide(n, Choice.OURS if ours else Choice.THEIRS))
        row = QHBoxLayout()
        row.setContentsMargins(8, 0, 8, 0)
        if ours:
            row.addStretch(1)
        row.addWidget(button)
        if not ours:
            row.addStretch(1)
        v.addLayout(row)
        return card

    def _choice_card(self, n: int, seg: Segment) -> QFrame:
        c = colors()
        block = seg.block
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {c.decide_bg}; border: 1.5px dashed {c.decide_border}; border-radius: 8px; }}"
            f" QLabel {{ border: none; background: transparent; color: {c.decide_fg}; }}"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)
        title = QLabel("Choose what goes here")
        title.setStyleSheet("font-weight: 600;")
        v.addWidget(title)
        detail = QLabel(
            f"Both sides changed this: {len(block.ours)} line{'s' if len(block.ours) != 1 else ''} ours, {len(block.theirs)} theirs."
        )
        v.addWidget(detail)
        if self.show_base and block.base is not None:
            base_label = QLabel("Common ancestor")
            base_label.setStyleSheet("font-weight: 600;")
            v.addWidget(base_label)
            v.addWidget(CodeCell(block.base, muted=True) if block.base else QLabel("(empty)"))
        options: list[tuple[str, Decision]] = [
            ("←  Ours", Choice.OURS),
            ("Theirs  →", Choice.THEIRS),
            ("Ours, then theirs", Choice.BOTH),
            ("Theirs, then ours", "".join(block.theirs + block.ours)),
        ]
        if block.base is not None:
            options.append(("Base", Choice.BASE))
        options.append(("Edit by hand", "".join(block.ours + block.theirs)))
        # Two rows of three so every label fits even in a narrow centre column.
        buttons = QGridLayout()
        buttons.setHorizontalSpacing(6)
        buttons.setVerticalSpacing(6)
        for i, (label, decision) in enumerate(options):
            b = QPushButton(label)
            b.setProperty("variant", "secondary")
            b.setToolTip(label)
            b.clicked.connect(lambda _=False, d=decision: self.decide(n, d))
            buttons.addWidget(b, i // 3, i % 3)
        v.addLayout(buttons)
        return card

    def _resolved_card(self, n: int, seg: Segment, decision: Decision) -> tuple[QFrame, int]:
        c = colors()
        if isinstance(decision, Choice):
            lines = [_display(line) for line in seg.block.lines_for(decision)]
            bg, border = {
                Choice.OURS: (c.ours_bg, c.ours_border),
                Choice.THEIRS: (c.theirs_bg, c.theirs_border),
            }.get(decision, (c.field_bg, c.border_strong))
            how = f"Resolved with {CHOICE_LABELS[decision]}"
        else:
            lines = decision.replace("\r\n", "\n").split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            bg, border = c.field_bg, c.border_strong
            how = "Edited by hand"
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
            f" QLabel {{ border: none; background: transparent; }}"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(4, 4, 8, 4)
        v.setSpacing(2)
        editor = AutoHeightEdit("\n".join(lines))
        editor.setAccessibleName(f"Resolution for conflict {n + 1}")
        editor.textChanged.connect(lambda e=editor, k=n: self._typed(k, e.toPlainText()))
        v.addWidget(editor)
        note = QLabel(how, objectName="Muted")
        undo = QPushButton("Undo")
        undo.setFlat(True)
        undo.setStyleSheet(
            f"QPushButton {{ color: {c.accent}; text-decoration: underline; border: none; padding: 0 4px; background: transparent; }}"
        )
        undo.clicked.connect(lambda: self.undo(n))
        foot = QHBoxLayout()
        foot.setContentsMargins(10, 0, 0, 2)
        foot.addWidget(note)
        foot.addStretch(1)
        foot.addWidget(undo)
        v.addLayout(foot)
        return card, len(lines)

    def _conflict_row(self, st: FileState, n: int, seg: Segment) -> tuple[QWidget, int]:
        decision = st.decisions.get(n)
        ours_cell = _column_cell("ColOurs", self._side_card(seg.block.ours, True, n), (8, 6, 8, 6))
        theirs_cell = _column_cell("ColTheirs", self._side_card(seg.block.theirs, False, n), (8, 6, 8, 6))
        if decision is None:
            result_cell = _column_cell("ColResult", self._choice_card(n, seg), (8, 6, 8, 6))
            produced = 0  # nothing decided yet, so no result lines to number
        else:
            card, produced = self._resolved_card(n, seg, decision)
            result_cell = _column_cell("ColResult", card, (8, 6, 8, 6))
        return _row((ours_cell, result_cell, theirs_cell)), produced

    # ------------------------------------------------------------------ decisions

    def decide(self, n: int, decision: Decision) -> None:
        if not self.current:
            return
        self.current.decisions[n] = decision
        undecided = self.current.undecided
        later = [k for k in undecided if k > n]
        self.focus = later[0] if later else (undecided[0] if undecided else n)
        self._render()
        self._scroll_to_focus()

    def decide_all(self, choice: Choice) -> None:
        if not self.current or self.current.special:
            return
        for n in self.current.undecided:
            self.current.decisions[n] = choice
        self._render()

    def undo(self, n: int) -> None:
        if self.current and n in self.current.decisions:
            del self.current.decisions[n]
            self.focus = n
            self._render()

    def _typed(self, n: int, text: str) -> None:
        if self.current:
            self.current.decisions[n] = text  # typing turns a quick pick into a hand edit
            self._update_controls()

    # ------------------------------------------------------------------ navigation and modes

    def _jump(self, step: int) -> None:
        st = self.current
        if not st or not st.conflict_count:
            return
        self.focus = (self.focus + step) % st.conflict_count
        self._update_controls()
        self._scroll_to_focus()

    def _scroll_to_focus(self) -> None:
        if 0 <= self.focus < len(self.conflict_rows):
            self.scroll.ensureWidgetVisible(self.conflict_rows[self.focus], 0, 80)

    def _toggle_base(self, on: bool) -> None:
        self.show_base = on
        if self.current and not self.current.special and not self.raw_btn.isChecked():
            self._render_rows(self.current)

    def _toggle_raw(self, on: bool) -> None:
        st = self.current
        if not st or st.special:
            return
        if on:
            self.raw.setPlainText(st.result_text().replace("\r\n", "\n"))
            self.heads.hide()
            self.body.setCurrentWidget(self.raw)
        else:
            # Back to conflict-by-conflict: whatever markers remain become the conflicts.
            st.set_text(self.raw.toPlainText().replace("\n", st.eol))
            self._render()
        self._update_controls()

    def _update_controls(self) -> None:
        st = self.current
        active = st is not None and not st.resolved and not st.special
        raw = self.raw_btn.isChecked()
        for w in (self.prev_btn, self.next_btn, self.base_box, self.all_ours_btn, self.all_theirs_btn):
            w.setVisible(active and not raw)
        self.raw_btn.setVisible(active)
        self.mark_btn.setVisible(active)
        if not active:
            self.counter.setText("")
            self._update_finish()
            return
        if raw:
            self.counter.setText("Editing the whole file · leftover markers stay as typed")
        elif st.conflict_count:
            left = len(st.undecided)
            self.counter.setText(
                f"Conflict {min(self.focus, st.conflict_count - 1) + 1} of {st.conflict_count}"
                + (f" · {left} left" if left else " · all decided")
            )
        else:
            self.counter.setText("No conflict markers in this file")
        ready = raw or not st.undecided
        self.mark_btn.setText(f"Mark {st.path.rsplit('/', 1)[-1]} resolved")
        self.mark_btn.setProperty("variant", "primary" if ready else "secondary")
        self.mark_btn.style().unpolish(self.mark_btn)
        self.mark_btn.style().polish(self.mark_btn)
        self._update_finish()

    def _update_finish(self) -> None:
        left = len(self.paths) - self._resolved_count()
        self.finish_btn.setProperty("variant", "primary" if left == 0 else "secondary")
        self.finish_btn.style().unpolish(self.finish_btn)
        self.finish_btn.style().polish(self.finish_btn)

    # ------------------------------------------------------------------ special files

    def _render_special(self, st: FileState) -> None:
        self.heads.hide()
        while self.special_layout.count():
            item = self.special_layout.takeAt(0)
            if item.widget():
                _discard(item.widget())
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        _discard(child.widget())
        if st.special == "binary":
            text = "This is a binary file, so it can't be merged line by line. Keep one side's version."
            options = [("Keep ours", st.ours), ("Keep theirs", st.theirs)]
        else:
            side = "your branch" if st.ours is None else "the incoming side"
            text = f"This file was deleted on {side} and changed on the other. Keep the changed file, or delete it."
            kept = st.ours if st.ours is not None else st.theirs
            options = [("Keep the changed file", kept), ("Delete the file", None)]
        label = QLabel(f"<b>{st.path}</b><br>{text}")
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        self.special_layout.addWidget(label)
        row = QHBoxLayout()
        for caption, data in options:
            b = QPushButton(caption)
            b.setProperty("variant", "secondary")
            b.clicked.connect(lambda _=False, d=data: self._save(st, d))
            row.addWidget(b)
        row.addStretch(1)
        self.special_layout.addLayout(row)
        self.special_layout.addStretch(1)
        self.body.setCurrentWidget(self.special)

    # ------------------------------------------------------------------ saving and finishing

    def result_text(self) -> str:
        st = self.current
        if st is None:
            return ""
        if self.raw_btn.isChecked():
            return self.raw.toPlainText().replace("\n", st.eol)
        return st.result_text()

    def mark_resolved(self) -> None:
        st = self.current
        if not st or st.special:
            return
        text = self.result_text()
        if not self.raw_btn.isChecked() and st.undecided:
            answer = QMessageBox.warning(
                self,
                "Conflicts remain",
                f"{len(st.undecided)} conflict(s) in {st.path} have no decision and will keep their markers. "
                "Mark it resolved anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            data = text.encode(st.encoding)
        except UnicodeEncodeError as exc:
            bad = exc.object[exc.start : exc.end]
            QMessageBox.warning(
                self,
                "Can't save these characters",
                f"“{bad}” can't be stored in this file's encoding ({st.encoding}). Remove or replace it, then try again.",
            )
            return
        self._save(st, data)

    def _save(self, st: FileState, data: bytes | None) -> None:
        try:
            self.backend.mark_resolved(st.path, data)
        except GitError as exc:
            QMessageBox.warning(self, "Git", exc.stderr or str(exc))
            return
        st.resolved = True
        self.file_resolved.emit(st.path)
        pending = [p for p in self.paths if not (p in self.states and self.states[p].resolved)]
        if pending:
            self.open_file(pending[0])
        else:
            self.current = None
            self._show_done()

    def _finish(self) -> None:
        left = len(self.paths) - self._resolved_count()
        if left:
            QMessageBox.information(self, "Not finished", f"Resolve the {left} remaining file(s) first.")
            return
        self.outcome = "commit"
        self.accept()

    def _abort(self) -> None:
        self.outcome = "abort"
        self.accept()


# Older callers use this name.
ConflictEditor = ConflictResolver
