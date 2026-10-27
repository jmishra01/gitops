from __future__ import annotations

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from gitops.core.encoding import pick_encoding
from gitops.core.git_backend import GitBackend
from gitops.ui.widgets.common import dialog_heading, set_tone


class GitignoreDialog(QDialog):
    """Edit .gitignore, and test which rule (if any) ignores a path."""

    def __init__(self, backend: GitBackend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.setWindowTitle(".gitignore")
        self.resize(640, 560)
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        path = backend.gitignore_path()
        self.original = path.read_bytes() if path.exists() else b""
        # Same rule as the conflict editor: UTF-8 if possible, otherwise Latin-1, which keeps every byte.
        self.encoding = pick_encoding(self.original)
        self.crlf = b"\r\n" in self.original
        self.editor.setPlainText(self.original.decode(self.encoding).replace("\r\n", "\n"))
        self.editor.setPlaceholderText("build/\n*.log\n.env")
        self.test_path = QLineEdit(placeholderText="Test a path, e.g. build/output.o")
        self.test_path.textChanged.connect(self._test)
        self.result = QLabel()
        self.result.setWordWrap(True)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_path, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("variant", "primary")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.editor, 1)
        layout.addLayout(test_row)
        layout.addWidget(self.result)
        layout.addWidget(buttons)
        dialog_heading(self, layout, ".gitignore", f"Files matching these rules are left out of git. {backend.gitignore_path()}")

    def _test(self, path: str) -> None:
        path = path.strip()
        if not path:
            self.result.clear()
            return
        # Tests the text being edited (unsaved), in a scratch repo.
        match = GitBackend.check_ignore_rules(self.editor.toPlainText(), path)
        if match:
            line, pattern = match
            set_tone(self.result, "warning")
            self.result.setText(f"Ignored by “{pattern}” (line {line})")
        else:
            set_tone(self.result, "success")
            self.result.setText("Not ignored")

    def _save(self) -> None:
        text = self.editor.toPlainText()
        if text and not text.endswith("\n"):
            text += "\n"
        if self.crlf:
            text = text.replace("\n", "\r\n")
        try:
            data = text.encode(self.encoding)
        except UnicodeEncodeError as exc:
            QMessageBox.warning(
                self,
                "Can't save these characters",
                f"“{exc.object[exc.start:exc.end]}” can't be stored in this file's encoding ({self.encoding}).",
            )
            return
        self.backend.gitignore_path().write_bytes(data)
        self.accept()
