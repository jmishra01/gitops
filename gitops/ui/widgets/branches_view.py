"""Branches page: local and remote branches, tags, worktrees and submodules, with a details panel of actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gitops.core.models import Branch, Submodule, Tag, Worktree
from gitops.ui.widgets.common import relative_time
from gitops.ui.widgets.row_list import Row, RowList


@dataclass(frozen=True)
class Action:
    key: str
    label: str
    variant: str = "secondary"  # primary, secondary, danger
    enabled: bool = True
    tip: str = ""


def tracking_chip(b: Branch) -> tuple[str, str]:
    """(text, chip kind) describing a local branch against its upstream."""
    if b.upstream_gone:
        return "Upstream gone", "danger"
    if not b.upstream:
        return "Not published", "muted"
    if b.ahead and b.behind:
        return f"↑{b.ahead} ↓{b.behind}", "warning"
    if b.behind:
        return f"↓{b.behind} behind", "warning"
    if b.ahead:
        return f"↑{b.ahead} to push", "accent"
    return "Up to date", "success"


def tip_line(b: Branch) -> str:
    if not b.timestamp:
        return b.target_sha[:7]
    return f"{b.subject} · {b.author} · {relative_time(b.date)}"


def build_sections(
    branches: list[Branch], tags: list[Tag], worktrees: list[Worktree], submodules: list[Submodule],
    root: Path | None, query: str = "",
) -> list[tuple[str, list[Row]]]:
    q = query.lower().strip()

    def keep(*texts: str) -> bool:
        return not q or any(q in t.lower() for t in texts)

    local, remote_groups = [], {}
    for b in branches:
        if not keep(b.name, b.subject):
            continue
        if b.is_remote:
            remote, _, short = b.name.partition("/")
            if short == "HEAD":
                continue
            remote_groups.setdefault(remote, []).append(
                Row(f"remote:{b.name}", "remote", short or b.name, tip_line(b), icon="cloud", data=b)
            )
        else:
            chip, chip_kind = tracking_chip(b)
            local.append(Row(f"local:{b.name}", "local", b.name, tip_line(b), chip, chip_kind, "branch", b.is_current, b))
    local.sort(key=lambda r: (not r.current, r.title.lower()))
    sections = [("Local branches", local)]
    sections += [(f"Remote · {name}", rows) for name, rows in sorted(remote_groups.items())]
    sections.append(("Tags", [
        Row(f"tag:{t.name}", "tag", t.name, t.message or ("Annotated tag" if t.annotated else "Lightweight tag"),
            t.target_sha[:7], icon="tag", data=t)
        for t in tags if keep(t.name, t.message)
    ]))
    if len(worktrees) > 1:
        rows = []
        for wt in worktrees:
            here = root is not None and Path(wt.path) == root
            chip, kind = ("This window", "accent") if here else ("Main", "muted") if wt.is_main else ("Locked", "warning") if wt.locked else ("", "muted")
            if keep(wt.branch or "", wt.path):
                rows.append(Row(f"worktree:{wt.path}", "worktree", wt.branch or "detached HEAD", wt.path, chip, kind, "folder", here, wt))
        sections.append(("Worktrees", rows))
    sections.append(("Submodules", [
        Row(f"submodule:{s.path}", "submodule", s.path, f"{s.sha[:7]} {s.describe}".strip(),
            "OK" if s.state == "ok" else s.state.capitalize(), "success" if s.state == "ok" else "warning", "folder", data=s)
        for s in submodules if keep(s.path)
    ]))
    return sections


def actions_for(row: Row, current: str | None, remotes: list[str], root: Path | None) -> list[Action]:
    if row.kind in ("local", "remote"):
        b: Branch = row.data
        out = []
        if row.kind == "remote":
            out.append(Action("checkout_remote", "Check out as local branch", "primary", tip="Create a local branch that tracks this one"))
        elif not b.is_current:
            out.append(Action("checkout", f"Switch to {b.name}", "primary"))
        if current and not b.is_current:
            out.append(Action("merge", f"Merge into {current}…", tip=f"Bring the commits of {b.name} into {current}"))
            out.append(Action("rebase", f"Rebase {current} onto this", tip=f"Replay {current}'s own commits on top of {b.name}"))
        out.append(Action("new_branch", "New branch from here…"))
        out.append(Action("show", "Show in history"))
        if row.kind == "local":
            out.append(Action("rename", "Rename…"))
            out.append(Action("delete", "Delete…", "danger", not b.is_current, "You can't delete the branch you're on" if b.is_current else ""))
        return out
    if row.kind == "tag":
        return [
            Action("show", "Show in history", "primary"),
            Action("new_branch", "New branch from here…"),
            *[Action(f"push_tag:{r}", f"Push to {r}") for r in remotes],
            Action("delete_tag", "Delete…", "danger"),
        ]
    if row.kind == "worktree":
        wt: Worktree = row.data
        here = root is not None and Path(wt.path) == root
        return [
            Action("open", "Open in this window", "primary", not here),
            Action("remove_worktree", "Remove…", "danger", not wt.is_main and not here,
                   "The main worktree and the open one can't be removed" if wt.is_main or here else ""),
        ]
    if row.kind == "submodule":
        return [Action("open", "Open submodule", "primary"), Action("update_submodules", "Update all submodules")]
    return []


class BranchesView(QWidget):
    action_requested = Signal(str, object)  # (action key, Row)
    new_branch_requested = Signal()
    add_worktree_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root: Path | None = None
        self.current: str | None = None
        self.remotes: list[str] = []
        self._data: tuple = ([], [], [], [])

        # ---- list: header with filter and the main create action
        title = QLabel("Branches", objectName="Title")
        self.filter_edit = QLineEdit(objectName="SearchField")
        self.filter_edit.setPlaceholderText("Filter branches, tags, worktrees")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(lambda _t: self._render())
        self.new_button = QPushButton("New branch…")
        self.new_button.setProperty("variant", "primary")
        self.new_button.clicked.connect(self.new_branch_requested.emit)
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 12, 16, 12)
        bar.setSpacing(10)
        bar.addWidget(title)
        bar.addSpacing(6)
        bar.addWidget(self.filter_edit, 1)
        bar.addWidget(self.new_button)
        self.list = RowList()
        self.list.current_row.connect(self._show_details)
        self.list.activated_row.connect(self._activate)
        self.list.menu_requested.connect(self._menu)
        list_panel = QFrame(objectName="Panel")
        list_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lp = QVBoxLayout(list_panel)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(0)
        lp.addLayout(bar)
        divider = QFrame(objectName="PanelDivider")
        divider.setFixedHeight(1)
        lp.addWidget(divider)
        lp.addWidget(self.list, 1)

        # ---- details of the selected row
        self.detail_title = QLabel(objectName="Title")
        self.detail_title.setWordWrap(True)
        self.detail_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_status = QLabel(objectName="Muted")
        self.detail_status.setWordWrap(True)
        self.detail_body = QLabel()
        self.detail_body.setWordWrap(True)
        self.detail_body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.buttons_box = QVBoxLayout()
        self.buttons_box.setSpacing(8)
        self.extra_button = QPushButton("Add worktree…")
        self.extra_button.setProperty("variant", "ghost")
        self.extra_button.clicked.connect(self.add_worktree_requested.emit)
        details = QWidget()
        dl = QVBoxLayout(details)
        dl.setContentsMargins(24, 20, 24, 20)
        dl.setSpacing(10)
        dl.addWidget(self.detail_title)
        dl.addWidget(self.detail_status)
        dl.addWidget(self.detail_body)
        dl.addSpacing(8)
        dl.addLayout(self.buttons_box)
        dl.addStretch(1)
        dl.addWidget(self.extra_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.empty = QLabel("Select a branch, tag or worktree", objectName="EmptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(self.empty)
        self.detail_stack.addWidget(details)
        detail_panel = QFrame(objectName="Panel")
        detail_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        QVBoxLayout(detail_panel).addWidget(self.detail_stack)

        split = QSplitter(objectName="branches_split")
        split.addWidget(list_panel)
        split.addWidget(detail_panel)
        split.setStretchFactor(0, 1)
        split.setSizes([700, 420])
        split.setHandleWidth(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)

    def set_data(self, root: Path | None, branches: list[Branch], remotes: list[str], tags: list[Tag],
                 worktrees: list[Worktree], submodules: list[Submodule]) -> None:
        self.root, self.remotes = root, list(remotes)
        self.current = next((b.name for b in branches if b.is_current), None)
        self._data = (branches, tags, worktrees, submodules)
        self._render()

    def _render(self) -> None:
        selected = self.list.current()
        keep = selected.key if selected else (f"local:{self.current}" if self.current else None)
        self.list.set_sections(build_sections(*self._data, self.root, self.filter_edit.text()), keep)

    def actions(self, row: Row) -> list[Action]:
        return actions_for(row, self.current, self.remotes, self.root)

    def _show_details(self, row: Row | None) -> None:
        while self.buttons_box.count():
            w = self.buttons_box.takeAt(0).widget()
            if w:
                w.hide()  # deleteLater alone leaves it painted until the event loop gets to it
                w.deleteLater()
        if row is None:
            self.detail_stack.setCurrentWidget(self.empty)
            return
        self.detail_stack.setCurrentIndex(1)
        self.detail_title.setText(row.title)
        status, body = self._describe(row)
        self.detail_status.setText(status)
        self.detail_body.setText(body)
        self.detail_body.setVisible(bool(body))
        for action in self.actions(row):
            button = QPushButton(action.label)
            button.setProperty("variant", action.variant)
            button.setEnabled(action.enabled)
            button.setToolTip(action.tip)
            button.clicked.connect(lambda _=False, k=action.key, r=row: self.action_requested.emit(k, r))
            self.buttons_box.addWidget(button)

    def _describe(self, row: Row) -> tuple[str, str]:
        if row.kind == "local":
            b: Branch = row.data
            if b.upstream_gone:
                status = f"Its upstream {b.upstream} was deleted on the remote."
            elif not b.upstream:
                status = "Only on this computer: not pushed to a remote yet."
            else:
                parts = [f"{b.ahead} to push" if b.ahead else "", f"{b.behind} to pull" if b.behind else ""]
                status = f"Tracks {b.upstream} · " + (", ".join(p for p in parts if p) or "up to date")
            if b.is_current:
                status = "Checked out · " + status
            return status, self._tip(b)
        if row.kind == "remote":
            b = row.data
            return f"Remote branch on {b.name.split('/', 1)[0]}", self._tip(b)
        if row.kind == "tag":
            t: Tag = row.data
            return ("Annotated tag" if t.annotated else "Lightweight tag") + f" on {t.target_sha[:7]}", t.message
        if row.kind == "worktree":
            wt: Worktree = row.data
            flags = [f for f, on in (("main worktree", wt.is_main), ("locked", wt.locked), ("missing on disk", wt.prunable)) if on]
            return (f"Worktree at {wt.path}" + (f" · {', '.join(flags)}" if flags else "")), f"HEAD {wt.head[:7]}"
        if row.kind == "submodule":
            s: Submodule = row.data
            return f"Submodule · {s.state}", f"{s.sha[:10]} {s.describe}".strip()
        return "", ""

    @staticmethod
    def _tip(b: Branch) -> str:
        if not b.timestamp:
            return b.target_sha[:10]
        return f"Last commit: {b.subject}\n{b.author} · {relative_time(b.date)} · {b.target_sha[:7]}"

    def _activate(self, row: Row) -> None:
        """Double-click / Enter runs the row's primary action."""
        first = next((a for a in self.actions(row) if a.enabled), None)
        if first and first.variant == "primary":
            self.action_requested.emit(first.key, row)

    def _menu(self, row: Row, global_pos) -> None:
        menu = QMenu(self)
        for action in self.actions(row):
            act = menu.addAction(action.label, lambda k=action.key: self.action_requested.emit(k, row))
            act.setEnabled(action.enabled)
            if action.variant == "danger":
                menu.insertSeparator(act)
        if row.kind == "worktree":
            menu.addAction("Add worktree…", self.add_worktree_requested.emit)
        menu.exec(global_pos)
