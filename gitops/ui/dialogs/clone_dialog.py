from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from gitops.ui.widgets.common import dialog_heading, set_tone


def repo_name_from_url(url: str) -> str:
    name = re.split(r"[/:]", url.rstrip("/"))[-1]
    return name.removesuffix(".git") or "repository"


class CloneDialog(QDialog):
    def __init__(self, default_parent: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clone repository")
        self.setMinimumWidth(520)
        self._name_edited = False

        self.url = QLineEdit(placeholderText="https://github.com/owner/repo.git")
        self.url.textChanged.connect(self._suggest_name)
        self.parent_dir = QLineEdit(default_parent)
        browse = QPushButton("Browse…")
        browse.setProperty("variant", "secondary")
        browse.setAutoDefault(False)  # Enter in the form means Clone
        browse.clicked.connect(self._browse)
        self.name = QLineEdit()
        self.name.textEdited.connect(lambda _t: setattr(self, "_name_edited", True))
        self.error = QLabel()
        self.error.setWordWrap(True)
        set_tone(self.error, "danger")

        where = QHBoxLayout()
        where.addWidget(self.parent_dir, 1)
        where.addWidget(browse)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        form.addRow("Repository URL", self.url)
        form.addRow("Clone into", where)
        form.addRow("Folder name", self.name)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        clone = buttons.addButton("Clone", QDialogButtonBox.ButtonRole.AcceptRole)
        clone.setDefault(True)
        clone.setProperty("variant", "primary")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        dialog_heading(self, layout, "Clone a repository", "Copy a repository from a server to this computer.")

    @property
    def destination(self) -> Path:
        return Path(self.parent_dir.text()).expanduser() / self.name.text().strip()

    def _suggest_name(self, url: str) -> None:
        if not self._name_edited:
            self.name.setText(repo_name_from_url(url) if url.strip() else "")

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Clone into", self.parent_dir.text())
        if chosen:
            self.parent_dir.setText(chosen)

    def _validate(self) -> None:
        if not self.url.text().strip():
            self.error.setText("Enter a repository URL.")
        elif not self.name.text().strip():
            self.error.setText("Enter a folder name.")
        elif not Path(self.parent_dir.text()).expanduser().is_dir():
            self.error.setText("The parent folder doesn't exist.")
        elif self.destination.exists() and any(self.destination.iterdir()):
            self.error.setText(f"{self.destination} already exists and isn't empty.")
        else:
            self.accept()
