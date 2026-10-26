from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QFontDatabase, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QPlainTextEdit


def _fmt(fg: str, bg: str | None = None) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(fg))
    if bg:
        fmt.setBackground(QColor(bg))
    return fmt


class DiffHighlighter(QSyntaxHighlighter):
    ADDED = _fmt("#1a7f37", "#e6ffec")
    REMOVED = _fmt("#cf222e", "#ffebe9")
    HUNK = _fmt("#8250df")
    META = _fmt("#6e7781")
    MATCH = _fmt("#000000", "#fff8c5")

    def __init__(self, document):
        super().__init__(document)
        self.match_pattern: re.Pattern[str] | None = None

    def highlightBlock(self, text: str) -> None:
        if text.startswith(("+++", "---", "diff ", "index ", "commit ", "Author:", "Date:")):
            self.setFormat(0, len(text), self.META)
        elif text.startswith("+"):
            self.setFormat(0, len(text), self.ADDED)
        elif text.startswith("-"):
            self.setFormat(0, len(text), self.REMOVED)
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self.HUNK)
        if self.match_pattern:
            for m in self.match_pattern.finditer(text):
                fmt = QTextCharFormat(self.format(m.start()))
                fmt.setBackground(self.MATCH.background())
                fmt.setFontWeight(QFont.Weight.Bold)
                self.setFormat(m.start(), m.end() - m.start(), fmt)


class DiffView(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.highlighter = DiffHighlighter(self.document())

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
