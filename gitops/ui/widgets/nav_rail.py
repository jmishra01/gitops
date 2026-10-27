"""Left navigation rail: one button per place in the app, with a count badge on Changes."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QLabel, QToolButton, QVBoxLayout

from gitops.ui import icons
from gitops.ui.theme import colors

PAGES = (
    ("changes", "Changes", "changes"),
    ("history", "History", "history"),
    ("files", "Files", "file"),
    ("blame", "Blame", "blame"),
    ("search", "Search", "search"),
)


class NavRail(QFrame):
    page_selected = Signal(str)
    dashboard_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, objectName="NavRail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(76)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(4)
        for key, label, icon_name in PAGES:
            button = self._button(label, icon_name, checkable=True)
            button.clicked.connect(lambda _=False, k=key: self.page_selected.emit(k))
            self.group.addButton(button)
            self.buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        self.repos = self._button("Repos", "grid", checkable=False)
        self.repos.setToolTip("Repositories dashboard (Ctrl+Shift+D)")
        self.repos.clicked.connect(self.dashboard_requested.emit)
        layout.addWidget(self.repos)

        self.badge = QLabel(self.buttons["changes"], objectName="RailBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setMinimumWidth(18)
        self.badge.setFixedHeight(18)
        self.badge.hide()
        self.apply_theme()

    def _button(self, label: str, icon_name: str, checkable: bool) -> QToolButton:
        button = QToolButton(objectName="RailButton")
        button.setText(label)
        button.setAccessibleName(label)
        button.setCheckable(checkable)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIconSize(QSize(20, 20))
        button.setFixedSize(60, 56)
        button.setProperty("icon_name", icon_name)
        return button

    def select(self, key: str) -> None:
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def set_badge(self, count: int) -> None:
        self.badge.setText(str(count) if count < 100 else "99+")
        self.badge.setVisible(count > 0)
        self.badge.adjustSize()
        self.badge.move(self.buttons["changes"].width() - self.badge.width() - 6, 3)

    def badge_text(self) -> str:
        return "" if self.badge.isHidden() else self.badge.text()

    def apply_theme(self) -> None:
        c = colors()
        for button in [*self.buttons.values(), self.repos]:
            button.setIcon(icons.icon(button.property("icon_name"), c.text_secondary, 20, c.accent_on_soft))
