from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QVBoxLayout

from gitops.core.git_backend import GitBackend, GitError
from gitops.core.models import ReflogEntry
from gitops.ui.widgets.common import dialog_heading, item, make_table, relative_time
from gitops.ui.widgets.history_view import TextDelegate


class UndoDialog(QDialog):
    """Every recent position of the current branch; restore any of them.

    Backed by git's reflog, so commits, amends, merges, rebases, resets and
    cherry-picks can all be undone, even after history was rewritten.
    """

    def __init__(self, backend: GitBackend, parent=None):
        super().__init__(parent)
        self.backend = backend
        branch = backend.current_branch() or "HEAD"
        self.setWindowTitle(f"Undo · history of {branch}")
        self.resize(780, 460)
        self.entries: list[ReflogEntry] = backend.reflog()

        self.table, model = make_table(["", "Action", "Commit", "When"], 1)
        for n, e in enumerate(self.entries):
            model.appendRow(
                [
                    item("current" if n == 0 else e.selector),
                    item(e.action, tooltip=e.action),
                    item(e.sha[:8], tooltip=e.sha),
                    item(relative_time(e.date), tooltip=e.date.strftime("%Y-%m-%d %H:%M:%S")),
                ]
            )
        self.table.setObjectName("CommitTable")
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setItemDelegate(TextDelegate(self.table))  # soft selection, like History
        header = self.table.horizontalHeader()
        for column, width in ((0, 96), (2, 96), (3, 110)):
            header.setSectionResizeMode(column, header.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        if len(self.entries) > 1:
            self.table.selectRow(1)
        self.table.doubleClicked.connect(lambda _i: self._restore())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        restore = buttons.addButton("Restore to selected", QDialogButtonBox.ButtonRole.ActionRole)
        restore.setDefault(True)
        restore.setProperty("variant", "primary")
        restore.clicked.connect(self._restore)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)
        dialog_heading(
            self,
            layout,
            f"Undo on {branch}",
            f"Every place {branch} has pointed, newest first. Restoring moves the branch back to that point; "
            "uncommitted changes are kept, and git refuses rather than overwrite them.",
        )

    def _restore(self) -> None:
        row = self.table.currentIndex().row()
        if row <= 0 or row >= len(self.entries):
            return
        entry = self.entries[row]
        answer = QMessageBox.question(
            self, "Restore", f"Move the branch back to {entry.sha[:8]} (before “{self.entries[row - 1].action}”)?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.backend.restore_to(entry.sha)
        except GitError as exc:
            QMessageBox.warning(
                self, "Couldn't restore", f"{exc.stderr}\n\nCommit or stash your local changes, then try again."
            )
            return
        self.accept()
