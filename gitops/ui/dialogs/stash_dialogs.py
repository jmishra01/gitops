from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from gitops.core.git_backend import GitBackend
from gitops.core.models import Stash
from gitops.ui.widgets.common import relative_time
from gitops.ui.widgets.diff_view import DiffView


class StashSaveDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stash changes")
        self.setMinimumWidth(420)
        self.message = QLineEdit(placeholderText="Half-done login refactor")
        self.include_untracked = QCheckBox("Include untracked files", checked=True)
        self.keep_index = QCheckBox("Keep staged changes in the working tree")
        form = QFormLayout(self)
        form.addRow(QLabel("Your changes are saved and the working tree goes back to the last commit."))
        form.addRow("Message", self.message)
        form.addRow(self.include_untracked)
        form.addRow(self.keep_index)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("Stash", QDialogButtonBox.ButtonRole.AcceptRole).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class StashViewDialog(QDialog):
    """Preview a stash, then apply, pop or drop it. `chosen` holds the action picked."""

    APPLY, POP, DROP = "apply", "pop", "drop"

    def __init__(self, backend: GitBackend, stash: Stash, parent=None):
        super().__init__(parent)
        self.chosen: str | None = None
        self.setWindowTitle(f"{stash.ref}: {stash.message}")
        self.resize(900, 640)
        info = QLabel(f"{stash.message} · saved {relative_time(stash.date)}")
        diff = DiffView()
        diff.show_async(lambda: backend.stash_diff(stash.ref))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        for label, action, tip in (
            ("Apply", self.APPLY, "Restore these changes and keep the stash"),
            ("Pop", self.POP, "Restore these changes and remove the stash"),
            ("Drop…", self.DROP, "Delete this stash without applying it"),
        ):
            button = buttons.addButton(label, QDialogButtonBox.ButtonRole.ActionRole)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, a=action: self._choose(a))
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(diff, 1)
        layout.addWidget(buttons)

    def _choose(self, action: str) -> None:
        self.chosen = action
        self.accept()
