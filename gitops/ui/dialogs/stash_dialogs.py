from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)

from gitops.ui.widgets.common import dialog_heading


class StashSaveDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stash changes")
        self.setMinimumWidth(420)
        self.message = QLineEdit(placeholderText="Half-done login refactor")
        self.include_untracked = QCheckBox("Include untracked files", checked=True)
        self.keep_index = QCheckBox("Keep staged changes in the working tree")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Message", self.message)
        form.addRow(self.include_untracked)
        form.addRow(self.keep_index)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        stash = buttons.addButton("Stash", QDialogButtonBox.ButtonRole.AcceptRole)
        stash.setDefault(True)
        stash.setProperty("variant", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addSpacing(6)
        layout.addWidget(buttons)
        dialog_heading(
            self,
            layout,
            "Stash changes",
            "Your changes are saved aside and the working tree goes back to the last commit.",
        )
