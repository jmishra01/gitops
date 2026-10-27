from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from gitops.core.rebase import RebaseStep
from gitops.ui.theme import colors
from gitops.ui.widgets.common import dialog_heading, set_tone

STEP_ROLE = Qt.ItemDataRole.UserRole
ACTION_COLORS = {"pick": "muted", "reword": "accent", "squash": "hunk", "fixup": "hunk", "drop": "danger"}


class RebaseDialog(QDialog):
    """Reorder, reword, squash, fix up or drop commits. Oldest is at the top, like git's todo list."""

    def __init__(self, steps: list[RebaseStep], base_label: str, has_merges: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit history")
        self.resize(760, 520)

        self.warning = QLabel()
        set_tone(self.warning, "warning")
        self.warning.setWordWrap(True)
        if has_merges:
            self.warning.setText("This range contains merge commits. They'll be flattened into a straight line.")

        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.model().rowsMoved.connect(lambda *_: self._validate())
        for step in steps:
            it = QListWidgetItem()
            it.setData(STEP_ROLE, step)
            self.list.addItem(it)
            self._render(it)

        buttons_col = QVBoxLayout()
        for label, action, tip in (
            ("Pick", "pick", "Keep the commit as it is"),
            ("Reword…", "reword", "Keep the changes, change the message"),
            ("Squash", "squash", "Fold into the commit above and combine messages"),
            ("Fixup", "fixup", "Fold into the commit above and drop this message"),
            ("Drop", "drop", "Remove the commit and its changes"),
        ):
            button = QPushButton(label)
            button.setProperty("variant", "danger" if action == "drop" else "secondary")
            button.setAutoDefault(False)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, a=action: self._set_action(a))
            buttons_col.addWidget(button)
        buttons_col.addSpacing(12)
        up = QPushButton("Move up")
        up.setProperty("variant", "ghost")
        up.setAutoDefault(False)
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("Move down")
        down.setProperty("variant", "ghost")
        down.setAutoDefault(False)
        down.clicked.connect(lambda: self._move(1))
        buttons_col.addWidget(up)
        buttons_col.addWidget(down)
        buttons_col.addStretch(1)

        middle = QHBoxLayout()
        middle.addWidget(self.list, 1)
        middle.addLayout(buttons_col)

        self.error = QLabel()
        set_tone(self.error, "danger")
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.start_btn = box.addButton("Rewrite history", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start_btn.setDefault(True)
        self.start_btn.setProperty("variant", "primary")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.warning)
        layout.addLayout(middle, 1)
        layout.addWidget(self.error)
        layout.addWidget(box)
        dialog_heading(
            self, layout, "Edit history",
            f"Commits after <b>{base_label}</b>, oldest first. Drag to reorder, then choose what happens to each one. "
            "Squash and fixup fold a commit into the one above it.",
        )
        self.warning.setVisible(has_merges)
        if self.list.count():
            self.list.setCurrentRow(0)
        self._validate()

    def steps(self) -> list[RebaseStep]:
        return [self.list.item(i).data(STEP_ROLE) for i in range(self.list.count())]

    def _render(self, it: QListWidgetItem) -> None:
        step: RebaseStep = it.data(STEP_ROLE)
        subject = step.message.split("\n", 1)[0] if step.action == "reword" and step.message else step.commit.subject
        indent = "    ↳ " if step.action in ("squash", "fixup") else ""
        it.setText(f"{step.action:<7} {step.commit.short_sha}  {indent}{subject}")
        it.setForeground(QBrush(QColor(getattr(colors(), ACTION_COLORS[step.action]))))
        font = it.font()
        font.setStrikeOut(step.action == "drop")
        it.setFont(font)

    def _set_action(self, action: str) -> None:
        items = self.list.selectedItems()
        if action == "reword" and len(items) == 1:
            step: RebaseStep = items[0].data(STEP_ROLE)
            current = step.message or step.commit.subject
            text, ok = QInputDialog.getMultiLineText(self, "Reword commit", "New commit message:", current)
            if not ok or not text.strip():
                return
            step.message = text.strip()
        for it in items:
            step = it.data(STEP_ROLE)
            step.action = action if action != "reword" or step.message else "pick"
            self._render(it)
        self._validate()

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.list.count():
            return
        it = self.list.takeItem(row)
        self.list.insertItem(target, it)
        self.list.setCurrentRow(target)
        self._validate()

    def _validate(self) -> None:
        steps = self.steps()
        for i in range(self.list.count()):
            self._render(self.list.item(i))
        kept = [s for s in steps if s.action != "drop"]
        problem = ""
        if kept and kept[0].action in ("squash", "fixup"):
            problem = "The first kept commit can't be squashed or fixed up: there's nothing above it to fold into."
        elif not kept and steps:
            problem = "Every commit is dropped. Use reset instead if that's what you want."
        self.error.setText(problem)
        self.start_btn.setEnabled(not problem and bool(steps))
