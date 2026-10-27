from __future__ import annotations

import re
from collections.abc import Callable

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QColor, QFont, QFontDatabase, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QPlainTextEdit

from gitops.services.task_runner import Task
from gitops.ui.theme import colors


def _fmt(fg: str, bg: str | None = None) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(fg))
    if bg:
        fmt.setBackground(QColor(bg))
    return fmt


class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.match_pattern: re.Pattern[str] | None = None
        self.load_theme()

    def load_theme(self) -> None:
        c = colors()
        self.added = _fmt(c.add_fg, c.add_bg)
        self.removed = _fmt(c.del_fg, c.del_bg)
        self.hunk = _fmt(c.hunk)
        self.meta = _fmt(c.muted)
        self.match_bg = QColor(c.match_bg)

    def highlightBlock(self, text: str) -> None:
        if text.startswith(("+++", "---", "diff ", "index ", "commit ", "Author:", "Date:")):
            self.setFormat(0, len(text), self.meta)
        elif text.startswith("+"):
            self.setFormat(0, len(text), self.added)
        elif text.startswith("-"):
            self.setFormat(0, len(text), self.removed)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self.hunk)
        if self.match_pattern:
            for m in self.match_pattern.finditer(text):
                fmt = QTextCharFormat(self.format(m.start()))
                fmt.setBackground(self.match_bg)
                fmt.setFontWeight(QFont.Weight.Bold)
                self.setFormat(m.start(), m.end() - m.start(), fmt)


class DiffView(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.highlighter = DiffHighlighter(self.document())
        self._generation = 0
        self._task: Task | None = None

    def apply_theme(self) -> None:
        self.highlighter.load_theme()
        self.highlighter.rehighlight()

    def show_async(self, fn: Callable[[], str], match: re.Pattern[str] | None = None) -> None:
        """Load diff text off the UI thread; a newer request supersedes older ones."""
        self._generation += 1
        generation = self._generation
        if self._task:
            self._task.cancel()
        self.setPlainText("Loading…")

        def load() -> tuple[int, str, re.Pattern[str] | None]:
            try:
                return generation, fn(), match
            except Exception as exc:  # shown in place of the diff
                return generation, f"Couldn't load diff: {exc}", None

        task = Task(load)
        # A bound method (not a lambda) so Qt drops the connection if this view is destroyed first.
        task.signals.result.connect(self._on_loaded)
        self._task = task
        QThreadPool.globalInstance().start(task)

    def _on_loaded(self, result: tuple[int, str, re.Pattern[str] | None]) -> None:
        generation, text, match = result
        if generation == self._generation:
            self.show_diff(text, match)

    def show_diff(self, text: str, match: re.Pattern[str] | None = None) -> None:
        self.highlighter.match_pattern = match
        self.setPlainText(text)
        if match:
            found = match.search(text)
            if found:
                cursor = self.textCursor()
                cursor.setPosition(found.start())
                self.setTextCursor(cursor)
                self.centerCursor()
