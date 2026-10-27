"""Stashes page: saved work-in-progress, a preview of each stash, and apply / pop / drop."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gitops.core.git_backend import GitBackend
from gitops.core.models import Stash
from gitops.ui.widgets.common import relative_time
from gitops.ui.widgets.diff_view import DiffView
from gitops.ui.widgets.row_list import Row, RowList

# "On main: message" (git stash push -m) or "WIP on main: abc1234 subject" (no message given)
_STASH_MESSAGE = re.compile(r"^On ([^:]+): (.*)$", re.DOTALL)
_STASH_WIP = re.compile(r"^WIP on ([^:]+): [0-9a-f]+ (.*)$", re.DOTALL)


def split_message(message: str) -> tuple[str | None, str]:
    """(branch the stash was made on, a readable title without git's prefix)."""
    if m := _STASH_MESSAGE.match(message):
        return m.group(1), m.group(2)
    if m := _STASH_WIP.match(message):
        return m.group(1), f"Work in progress on “{m.group(2)}”"
    return None, message


def stash_row(stash: Stash) -> Row:
    branch, text = split_message(stash.message)
    subtitle = " · ".join(p for p in (stash.ref, f"on {branch}" if branch else "", relative_time(stash.date)) if p)
    return Row(stash.ref, "stash", text or "(no message)", subtitle, icon="stash", data=stash)


class StashesView(QWidget):
    action_requested = Signal(str, object)  # ("apply" | "pop" | "drop", Stash)
    save_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.backend: GitBackend | None = None

        title = QLabel("Stashes", objectName="Title")
        self.count_label = QLabel(objectName="Muted")
        self.save_button = QPushButton("Stash changes…")
        self.save_button.setProperty("variant", "primary")
        self.save_button.setToolTip("Put your uncommitted changes aside and get a clean working tree")
        self.save_button.clicked.connect(self.save_requested.emit)
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 12, 16, 12)
        bar.setSpacing(10)
        bar.addWidget(title)
        bar.addWidget(self.count_label, 1)
        bar.addWidget(self.save_button)
        self.list = RowList()
        self.list.current_row.connect(self._show)
        self.list.activated_row.connect(lambda row: self.action_requested.emit("apply", row.data))
        self.list.menu_requested.connect(self._menu)
        self.list_empty = QLabel("No stashes.\nStash your changes to switch tasks without committing.", objectName="EmptyState")
        self.list_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.list)
        self.list_stack.addWidget(self.list_empty)
        list_panel = QFrame(objectName="Panel")
        list_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lp = QVBoxLayout(list_panel)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(0)
        lp.addLayout(bar)
        divider = QFrame(objectName="PanelDivider")
        divider.setFixedHeight(1)
        lp.addWidget(divider)
        lp.addWidget(self.list_stack, 1)

        # ---- preview with its actions
        self.preview_title = QLabel(objectName="SectionLabel")
        self.preview_title.setWordWrap(True)
        self.buttons: dict[str, QPushButton] = {}
        actions = QHBoxLayout()
        actions.setSpacing(8)
        for key, label, variant, tip in (
            ("apply", "Apply", "primary", "Restore these changes and keep the stash"),
            ("pop", "Pop", "secondary", "Restore these changes and remove the stash"),
            ("drop", "Drop…", "danger", "Delete this stash without applying it"),
        ):
            button = QPushButton(label)
            button.setProperty("variant", variant)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, k=key: (s := self.selected()) and self.action_requested.emit(k, s))
            self.buttons[key] = button
            actions.addWidget(button)
        head = QHBoxLayout()
        head.setContentsMargins(16, 12, 16, 12)
        head.addWidget(self.preview_title, 1)
        head.addLayout(actions)
        self.diff = DiffView()
        self.diff.setFrameShape(QFrame.Shape.NoFrame)
        preview = QWidget()
        pv = QVBoxLayout(preview)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        pv.addLayout(head)
        divider = QFrame(objectName="PanelDivider")
        divider.setFixedHeight(1)
        pv.addWidget(divider)
        pv.addWidget(self.diff, 1)
        self.empty = QLabel("Select a stash to preview its changes", objectName="EmptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_stack = QStackedWidget()
        self.preview_stack.addWidget(self.empty)
        self.preview_stack.addWidget(preview)
        preview_panel = QFrame(objectName="Panel")
        preview_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        ppl = QVBoxLayout(preview_panel)
        ppl.setContentsMargins(0, 0, 0, 0)
        ppl.addWidget(self.preview_stack)

        split = QSplitter(objectName="stashes_split")
        split.addWidget(list_panel)
        split.addWidget(preview_panel)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 700])
        split.setHandleWidth(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)

    def set_backend(self, backend: GitBackend | None) -> None:
        self.backend = backend
        self.set_stashes([])

    def set_stashes(self, stashes: list[Stash]) -> None:
        selected = self.list.current()
        # Refs shift when a stash is dropped, so re-select by commit rather than by stash@{n}.
        keep_sha = selected.data.sha if selected else None
        rows = [stash_row(s) for s in stashes]
        keep = next((r.key for r in rows if r.data.sha == keep_sha), rows[0].key if rows else None)
        self.list.set_sections([("Saved", rows)], keep)
        self.count_label.setText(f"{len(stashes)} saved" if stashes else "")
        self.list_stack.setCurrentWidget(self.list if stashes else self.list_empty)

    def selected(self) -> Stash | None:
        row = self.list.current()
        return row.data if row else None

    def _show(self, row: Row | None) -> None:
        if row is None or self.backend is None:
            self.preview_stack.setCurrentWidget(self.empty)
            return
        self.preview_stack.setCurrentIndex(1)
        self.preview_title.setText(f"{row.title}  ·  {row.subtitle}")
        backend, ref = self.backend, row.data.ref
        self.diff.show_async(lambda: backend.stash_diff(ref))

    def _menu(self, row: Row, global_pos) -> None:
        menu = QMenu(self)
        menu.addAction("Apply", lambda: self.action_requested.emit("apply", row.data))
        menu.addAction("Pop", lambda: self.action_requested.emit("pop", row.data))
        menu.addSeparator()
        menu.addAction("Drop…", lambda: self.action_requested.emit("drop", row.data))
        menu.exec(global_pos)
