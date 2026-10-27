"""Top bar: repository and branch switchers, a search/command field, and one Sync button that says what it will do."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QSizePolicy, QToolButton

from gitops.ui import icons
from gitops.ui.theme import colors


@dataclass(frozen=True)
class SyncState:
    """What the Sync button offers, derived from the branch's relationship to its upstream."""

    action: str  # "fetch", "pull", "push", "sync" (pull then push), "publish", "none"
    label: str
    tooltip: str

    @staticmethod
    def from_repo(has_remote: bool, ahead_behind: tuple[int, int] | None, branch: str | None) -> SyncState:
        if not has_remote:
            return SyncState("none", "No remote", "This repository has no remote to sync with")
        if ahead_behind is None:
            if branch:
                return SyncState("publish", "Publish branch", f"Push {branch} and start tracking it on the remote")
            return SyncState("fetch", "Fetch", "Detached HEAD: fetch to see what's new")
        ahead, behind = ahead_behind
        if ahead and behind:
            return SyncState("sync", f"Sync ↑{ahead} ↓{behind}", f"Pull {behind} commit(s), then push {ahead}")
        if behind:
            return SyncState("pull", f"Pull ↓{behind}", f"Bring in {behind} commit(s) from the remote")
        if ahead:
            return SyncState("push", f"Push ↑{ahead}", f"Send {ahead} commit(s) to the remote")
        return SyncState("fetch", "Up to date", "Fetch to check for new commits")


class TopBar(QFrame):
    search_requested = Signal(str)  # text typed so far (opens the command palette)
    sync_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, objectName="TopBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(54)

        self.logo = QLabel()
        self.logo.setFixedSize(28, 28)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.repo_menu = QMenu(self)
        self.repo_button = self._pill("Open a repository", "folder", self.repo_menu)
        self.repo_button.setAccessibleName("Repository")
        self.branch_menu = QMenu(self)
        self.branch_button = self._pill("No branch", "branch", self.branch_menu)
        self.branch_button.setAccessibleName("Branch")

        self.search = QLineEdit(objectName="SearchField")
        self.search.setPlaceholderText("Search commits, files, branches, or run a command      Ctrl+K")
        self.search.setAccessibleName("Search or run a command")
        self.search.setMaximumWidth(560)
        self.search.setMinimumWidth(240)
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search.textEdited.connect(self._typed)
        self.search.returnPressed.connect(lambda: self._typed(self.search.text()))

        self.sync_menu = QMenu(self)
        self.sync_button = QToolButton()
        self.sync_button.setProperty("variant", "sync")
        self.sync_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.sync_button.setMenu(self.sync_menu)
        self.sync_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sync_button.setIconSize(QSize(16, 16))
        self.sync_button.clicked.connect(self.sync_requested.emit)
        self.sync_state = SyncState("none", "No remote", "")

        self.undo_button = self._icon_button("undo", "Undo: restore the branch to an earlier point (Ctrl+Alt+Z)")
        self.console_button = self._icon_button("terminal", "Git console (Ctrl+`)")
        self.console_button.setCheckable(True)
        self.settings_menu = QMenu(self)
        self.settings_button = self._icon_button("settings", "Settings")
        self.settings_button.setMenu(self.settings_menu)
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        left = QHBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.logo)
        left.addWidget(self.repo_button)
        left.addWidget(self.branch_button)
        right = QHBoxLayout()
        right.setSpacing(4)
        right.addWidget(self.sync_button)
        right.addSpacing(4)
        for b in (self.undo_button, self.console_button, self.settings_button):
            right.addWidget(b)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 12, 8)
        layout.setSpacing(12)
        layout.addLayout(left)
        layout.addStretch(1)
        layout.addWidget(self.search, 4)
        layout.addStretch(1)
        layout.addLayout(right)
        self.apply_theme()

    # ------------------------------------------------------------------ building

    def _pill(self, text: str, icon_name: str, menu: QMenu) -> QToolButton:
        button = QToolButton()
        button.setProperty("variant", "pill")
        button.setProperty("icon_name", icon_name)
        button.setText(f"{text}  ▾")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(16, 16))
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return button

    def _icon_button(self, icon_name: str, tip: str) -> QToolButton:
        button = QToolButton()
        button.setProperty("variant", "ghost")
        button.setProperty("icon_name", icon_name)
        button.setToolTip(tip)
        button.setAccessibleName(tip.split(" (")[0].split(":")[0])
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(34, 34)
        return button

    def _typed(self, text: str) -> None:
        self.search_requested.emit(text)
        self.search.clear()

    # ------------------------------------------------------------------ state

    def set_repo(self, name: str | None) -> None:
        self.repo_button.setText(f"{name or 'Open a repository'}  ▾")

    def set_branch(self, branch: str | None) -> None:
        self.branch_button.setText(f"{branch or 'detached HEAD'}  ▾")

    def set_sync(self, state: SyncState, busy: str | None = None) -> None:
        self.sync_state = state
        self.sync_button.setText(busy or state.label)
        self.sync_button.setToolTip(state.tooltip)
        # Only a real next step gets the filled primary look; "No remote" and "Up to date" stay quiet.
        quiet = state.action == "none" or (state.action == "fetch" and state.label == "Up to date")
        variant = "pill" if quiet and not busy else "sync"
        if self.sync_button.property("variant") != variant:
            self.sync_button.setProperty("variant", variant)
            self.sync_button.style().unpolish(self.sync_button)
            self.sync_button.style().polish(self.sync_button)
            self.apply_theme()

    def apply_theme(self) -> None:
        c = colors()
        logo = icons.pixmap("logo", "#FFFFFF", 16, 2.2)
        self.logo.setPixmap(logo)
        self.logo.setStyleSheet(f"background: {c.primary}; border-radius: 8px;")
        for button in (self.repo_button, self.branch_button):
            button.setIcon(icons.icon(button.property("icon_name"), c.text_secondary, 16))
        for button in (self.undo_button, self.console_button, self.settings_button):
            button.setIcon(icons.icon(button.property("icon_name"), c.text_secondary, 18, c.accent_on_soft))
        quiet = self.sync_button.property("variant") == "pill"
        self.sync_button.setIcon(icons.icon("sync", c.text_secondary if quiet else "#FFFFFF", 16))
