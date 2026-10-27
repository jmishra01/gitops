"""Commit composer: summary with a length counter, optional description, amend, and a button that says what it does."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout

from gitops.ui.theme import colors

SUBJECT_LIMIT = 72


class CommitComposer(QFrame):
    commit_requested = Signal()
    amend_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent, objectName="Composer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        summary_label = QLabel("Summary")
        summary_label.setStyleSheet("font-weight: 600;")
        self.counter = QLabel()
        self.counter.setObjectName("Muted")
        self.summary = QLineEdit(placeholderText="Switch login to OAuth tokens")
        self.summary.setAccessibleName("Commit summary")
        summary_label.setBuddy(self.summary)
        self.summary.textChanged.connect(self._update_counter)

        description_label = QLabel("Description <span style='font-weight:400'>optional</span>")
        description_label.setTextFormat(Qt.TextFormat.RichText)
        description_label.setStyleSheet("font-weight: 600;")
        self.description = QPlainTextEdit(placeholderText="Why this change, and anything a reviewer should know")
        self.description.setAccessibleName("Commit description")
        self.description.setFixedHeight(72)
        self.description.setTabChangesFocus(True)

        self.amend = QCheckBox("Amend the last commit instead")
        self.amend.toggled.connect(self.amend_toggled.emit)
        self.button = QPushButton("Commit")
        self.button.setProperty("variant", "primary")
        self.button.setMinimumHeight(40)
        self.button.setToolTip("Ctrl+Enter")
        self.button.clicked.connect(self.commit_requested.emit)
        for widget in (self.summary, self.description):
            QShortcut(QKeySequence("Ctrl+Return"), widget, activated=self.commit_requested.emit)

        head = QHBoxLayout()
        head.addWidget(summary_label)
        head.addStretch(1)
        head.addWidget(self.counter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(8)
        layout.addLayout(head)
        layout.addWidget(self.summary)
        layout.addWidget(description_label)
        layout.addWidget(self.description)
        layout.addWidget(self.amend)
        layout.addWidget(self.button)
        self._update_counter()

    # ------------------------------------------------------------------ message

    def message(self) -> str:
        summary = self.summary.text().strip()
        body = self.description.toPlainText().strip()
        return f"{summary}\n\n{body}" if body else summary

    def set_message(self, text: str) -> None:
        lines = text.strip("\n").split("\n")
        self.summary.setText(lines[0].strip() if lines else "")
        rest = "\n".join(lines[1:]).strip("\n")
        self.description.setPlainText(rest)

    def is_empty(self) -> bool:
        return not self.summary.text().strip() and not self.description.toPlainText().strip()

    def clear(self) -> None:
        self.summary.clear()
        self.description.clear()

    def set_action(self, label: str) -> None:
        self.button.setText(label)

    def _update_counter(self) -> None:
        n = len(self.summary.text())
        over = n > SUBJECT_LIMIT
        self.counter.setText(f"{n} / {SUBJECT_LIMIT}" + (" · keep it shorter" if over else ""))
        self.counter.setStyleSheet(f"color: {colors().danger};" if over else "")

    def apply_theme(self) -> None:
        self._update_counter()
