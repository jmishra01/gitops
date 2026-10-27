from __future__ import annotations

import shlex
from datetime import datetime

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

# Commands that only read state. Hidden by default so the console shows what changed things.
READ_ONLY = {
    "status", "rev-parse", "log", "show", "diff", "for-each-ref", "rev-list", "blame", "grep",
    "remote", "check-ref-format", "reflog", "cat-file", "ls-files",
}


def is_read_only(args: list[str]) -> bool:
    sub = next((a for a in args if not a.startswith("-") and "=" not in a), "")
    if sub == "stash":
        return len(args) > 1 and args[args.index("stash") + 1] in ("list", "show")
    return sub in READ_ONLY


class CommandRelay(QObject):
    """Thread-safe bridge: git runs on worker threads, the console lives on the UI thread."""

    command = Signal(list)


class GitConsole(QWidget):
    """Shows the git commands GitOps runs for you, so you can learn and reproduce them."""

    MAX_LINES = 2000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.relay = CommandRelay(self)
        self.relay.command.connect(self.add)
        self.output = QPlainTextEdit(readOnly=True)
        self.output.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.output.setMaximumBlockCount(self.MAX_LINES)
        self.show_reads = QCheckBox("Show read-only commands")
        clear = QPushButton("Clear")
        clear.clicked.connect(self.output.clear)
        bar = QHBoxLayout()
        bar.addWidget(self.show_reads)
        bar.addStretch(1)
        bar.addWidget(clear)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self.output)

    def listener(self, args: list[str]) -> None:
        """Pass this to GitBackend.command_listeners; safe to call from any thread."""
        self.relay.command.emit(args)

    def add(self, args: list[str]) -> None:
        if is_read_only(args) and not self.show_reads.isChecked():
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.output.appendPlainText(f"{stamp}  $ git {shlex.join(args)}")
