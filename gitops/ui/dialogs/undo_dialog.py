from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox, QVBoxLayout

from gitops.core.git_backend import GitBackend, GitError
from gitops.core.models import ReflogEntry
from gitops.ui.widgets.common import item, make_table, relative_time


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

        intro = QLabel(
            f"Where <b>{branch}</b> has pointed, newest first. Restoring moves the branch back to that point; "
            "uncommitted changes are kept, and git refuses rather than overwrite them."
        )
        intro.setWordWrap(True)
        self.table, model = make_table(["", "Action", "Commit", "When"], 1)
        for n, e in enumerate(self.entries):
            model.appendRow([
                item("current" if n == 0 else e.selector),
                item(e.action, tooltip=e.action),
                item(e.sha[:8], tooltip=e.sha),
                item(relative_time(e.date), tooltip=e.date.strftime("%Y-%m-%d %H:%M:%S")),
            ])
        if len(self.entries) > 1:
            self.table.selectRow(1)
        self.table.doubleClicked.connect(lambda _i: self._restore())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        restore = buttons.addButton("Restore to selected", QDialogButtonBox.ButtonRole.ActionRole)
        restore.clicked.connect(self._restore)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)

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
