from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gitops.core.compare import is_binary
from gitops.core.conflicts import BASE_MARK, OURS_MARK, SPLIT_MARK, THEIRS_MARK, Choice, parse_conflicts, resolve, resolve_all
from gitops.core.git_backend import GitBackend, GitError
from gitops.ui.theme import colors


def _fmt(bg: str, fg: str | None = None, bold: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setBackground(QColor(bg))
    if fg:
        fmt.setForeground(QColor(fg))
    if bold:
        fmt.setFontWeight(700)
    return fmt


class ConflictHighlighter(QSyntaxHighlighter):
    """Tints the ours / base / theirs sections between conflict markers."""

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


def _pane(title: str, editor: QPlainTextEdit) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel(title)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    layout.addWidget(label)
    layout.addWidget(editor)
    return box


def _text(data: bytes | None, missing: str) -> str:
    if data is None:
        return missing
    if is_binary(data):
        return f"Binary file, {len(data):,} bytes"
    return data.decode("utf-8", errors="replace")


class ConflictEditor(QDialog):
    """Resolve one conflicted file: pick a side per conflict, edit freely, then mark resolved."""

    def __init__(self, backend: GitBackend, path: str, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.path = path
        self.setWindowTitle(f"Resolve conflicts · {path}")
        self.resize(1200, 820)

        self.base, self.ours, self.theirs = backend.conflict_versions(path)
        working = backend.root / path
        self.working = working.read_bytes() if working.is_file() else None
        self.binary = any(d is not None and is_binary(d) for d in (self.base, self.ours, self.theirs, self.working))

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        def viewer(data: bytes | None, missing: str) -> QPlainTextEdit:
            view = QPlainTextEdit(readOnly=True)
            view.setFont(mono)
            view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            view.setPlainText(_text(data, missing))
            return view

        top = QSplitter()
        top.addWidget(_pane("Ours · your branch", viewer(self.ours, "Deleted on your branch")))
        top.addWidget(_pane("Base · common ancestor", viewer(self.base, "Didn't exist in the common ancestor")))
        top.addWidget(_pane("Theirs · incoming", viewer(self.theirs, "Deleted on the incoming side")))

        self.result = QPlainTextEdit()
        self.result.setFont(mono)
        self.result.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.highlighter = ConflictHighlighter(self.result.document())
        if self.binary:
            self.result.setPlainText("Binary file: choose a whole version with the buttons below.")
            self.result.setReadOnly(True)
        else:
            self.result.setPlainText(_text(self.working, ""))
        self.result.textChanged.connect(self._update_status)
        self.result.cursorPositionChanged.connect(self._update_status)

        # Per-conflict actions
        self.prev_btn = QPushButton("◀ Previous")
        self.prev_btn.clicked.connect(lambda: self._jump(-1))
        self.next_btn = QPushButton("Next ▶")
        self.next_btn.clicked.connect(lambda: self._jump(1))
        self.counter = QLabel()
        self.choice_buttons: dict[Choice, QPushButton] = {}
        conflict_bar = QHBoxLayout()
        conflict_bar.addWidget(self.prev_btn)
        conflict_bar.addWidget(self.next_btn)
        conflict_bar.addWidget(self.counter, 1)
        for choice, label in ((Choice.OURS, "Use ours"), (Choice.THEIRS, "Use theirs"), (Choice.BOTH, "Use both"), (Choice.BASE, "Use base")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, c=choice: self._resolve_current(c))
            conflict_bar.addWidget(button)
            self.choice_buttons[choice] = button
        all_ours = QPushButton("All ours")
        all_ours.clicked.connect(lambda: self._resolve_all(Choice.OURS))
        all_theirs = QPushButton("All theirs")
        all_theirs.clicked.connect(lambda: self._resolve_all(Choice.THEIRS))
        conflict_bar.addWidget(all_ours)
        conflict_bar.addWidget(all_theirs)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.addLayout(conflict_bar)
        bottom_layout.addWidget(_pane("Result · this is what gets saved (editable)", self.result))

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(bottom)
        split.setSizes([300, 480])

        # Whole-file decisions and finishing
        take_ours = QPushButton("Take ours file" if self.ours is not None else "Delete file (as on your branch)")
        take_ours.clicked.connect(lambda: self._take_whole(self.ours))
        take_theirs = QPushButton("Take theirs file" if self.theirs is not None else "Delete file (as incoming)")
        take_theirs.clicked.connect(lambda: self._take_whole(self.theirs))
        self.resolve_btn = QPushButton("Mark resolved")
        self.resolve_btn.setDefault(True)
        self.resolve_btn.setEnabled(not self.binary)
        self.resolve_btn.clicked.connect(self._mark_resolved)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        footer = QHBoxLayout()
        footer.addWidget(take_ours)
        footer.addWidget(take_theirs)
        footer.addStretch(1)
        footer.addWidget(cancel)
        footer.addWidget(self.resolve_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(split, 1)
        layout.addLayout(footer)
        self._jump(1, from_start=True)
        self._update_status()

    def apply_theme(self) -> None:
        self.highlighter.load_theme()
        self.highlighter.rehighlight()

    # ------------------------------------------------------------------ conflicts

    def _blocks(self):
        return [] if self.binary else parse_conflicts(self.result.toPlainText())

    def _current_index(self) -> int | None:
        blocks = self._blocks()
        if not blocks:
            return None
        line = self.result.textCursor().blockNumber()
        for n, block in enumerate(blocks):
            if line <= block.end:
                return n
        return len(blocks) - 1

    def _update_status(self) -> None:
        blocks = self._blocks()
        current = self._current_index()
        has_base = current is not None and blocks[current].base is not None
        for choice, button in self.choice_buttons.items():
            button.setEnabled(current is not None and (choice is not Choice.BASE or has_base))
        self.prev_btn.setEnabled(len(blocks) > 0)
        self.next_btn.setEnabled(len(blocks) > 0)
        if self.binary:
            self.counter.setText("Binary conflict: take one side's file")
        elif blocks:
            self.counter.setText(f"Conflict {current + 1} of {len(blocks)}")
        else:
            self.counter.setText("No conflicts left · review the result, then mark resolved")

    def _jump(self, direction: int, from_start: bool = False) -> None:
        blocks = self._blocks()
        if not blocks:
            return
        line = -1 if from_start else self.result.textCursor().blockNumber()
        if direction > 0:
            target = next((b for b in blocks if b.start > line), blocks[0])
        else:
            target = next((b for b in reversed(blocks) if b.end < line), blocks[-1])
        cursor = QTextCursor(self.result.document().findBlockByNumber(target.start))
        self.result.setTextCursor(cursor)
        self.result.centerCursor()

    def _set_result(self, text: str, cursor_line: int) -> None:
        scroll = self.result.verticalScrollBar().value()
        self.result.setPlainText(text)
        block = self.result.document().findBlockByNumber(min(cursor_line, self.result.blockCount() - 1))
        self.result.setTextCursor(QTextCursor(block))
        self.result.verticalScrollBar().setValue(scroll)

    def _resolve_current(self, choice: Choice) -> None:
        current = self._current_index()
        if current is None:
            return
        start = self._blocks()[current].start
        self._set_result(resolve(self.result.toPlainText(), {current: choice}), start)
        self._jump(1)

    def _resolve_all(self, choice: Choice) -> None:
        if not self.binary:
            self._set_result(resolve_all(self.result.toPlainText(), choice), 0)

    # ------------------------------------------------------------------ finishing

    def _take_whole(self, data: bytes | None) -> None:
        action = "delete the file" if data is None else "replace the result with that whole version"
        if QMessageBox.question(self, "Take whole file", f"This will {action} and mark it resolved. Continue?") != QMessageBox.StandardButton.Yes:
            return
        self._save(data)

    def _mark_resolved(self) -> None:
        text = self.result.toPlainText()
        if parse_conflicts(text):
            answer = QMessageBox.warning(
                self,
                "Conflicts remain",
                "The result still has conflict markers. Mark it resolved anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        # QPlainTextEdit normalises line endings to \n; put CRLF back if the file used it.
        if self.working and b"\r\n" in self.working:
            text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        self._save(text.encode("utf-8"))

    def _save(self, data: bytes | None) -> None:
        try:
            self.backend.mark_resolved(self.path, data)
        except GitError as exc:
            QMessageBox.warning(self, "Git", exc.stderr or str(exc))
            return
        self.accept()
