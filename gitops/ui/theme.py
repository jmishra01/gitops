"""Light and dark themes.

Widgets never hard-code colors: they read `colors()` when painting, and any
widget with an `apply_theme()` method is refreshed when the theme changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

MODES = ("system", "light", "dark")


@dataclass(frozen=True)
class Colors:
    dark: bool
    success: str
    warning: str
    danger: str
    accent: str
    muted: str
    add_fg: str
    add_bg: str
    del_fg: str
    del_bg: str
    hunk: str
    match_bg: str
    banner_bg: str
    banner_border: str
    banner_fg: str
    ours_bg: str
    base_bg: str
    theirs_bg: str
    marker_bg: str
    age: tuple[str, ...]  # blame tints, newest first
    uncommitted_bg: str


LIGHT = Colors(
    dark=False,
    success="#1a7f37",
    warning="#9a6700",
    danger="#cf222e",
    accent="#0969da",
    muted="#6e7781",
    add_fg="#1a7f37",
    add_bg="#e6ffec",
    del_fg="#cf222e",
    del_bg="#ffebe9",
    hunk="#8250df",
    match_bg="#fff8c5",
    banner_bg="#fff8c5",
    banner_border="#d4a72c",
    banner_fg="#7d4e00",
    ours_bg="#ddf4ff",
    base_bg="#f6f8fa",
    theirs_bg="#dafbe1",
    marker_bg="#eaeef2",
    age=("#fff1e5", "#fff7ef", "#fffaf5", "#fdfdfd"),
    uncommitted_bg="#dafbe1",
)

DARK = Colors(
    dark=True,
    success="#3fb950",
    warning="#d29922",
    danger="#f85149",
    accent="#58a6ff",
    muted="#8b949e",
    add_fg="#7ee787",
    add_bg="#12261e",
    del_fg="#ffa198",
    del_bg="#2d1215",
    hunk="#d2a8ff",
    match_bg="#5a4a12",
    banner_bg="#2e2610",
    banner_border="#9e6a03",
    banner_fg="#e3b341",
    ours_bg="#0c2d4b",
    base_bg="#1c2128",
    theirs_bg="#12301d",
    marker_bg="#30363d",
    age=("#3b2410", "#2f2112", "#251d15", "#1e1e1e"),
    uncommitted_bg="#12301d",
)

_mode = "system"


def _system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:  # Qt < 6.5
        return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def is_dark() -> bool:
    return _mode == "dark" or (_mode == "system" and _system_is_dark())


def colors() -> Colors:
    return DARK if is_dark() else LIGHT


def mode() -> str:
    return _mode


def _palette(dark: bool) -> QPalette:
    p = QPalette()
    if dark:
        roles = {
            QPalette.ColorRole.Window: "#1f2328",
            QPalette.ColorRole.WindowText: "#e6edf3",
            QPalette.ColorRole.Base: "#0d1117",
            QPalette.ColorRole.AlternateBase: "#161b22",
            QPalette.ColorRole.Text: "#e6edf3",
            QPalette.ColorRole.Button: "#21262d",
            QPalette.ColorRole.ButtonText: "#e6edf3",
            QPalette.ColorRole.Highlight: "#1f6feb",
            QPalette.ColorRole.HighlightedText: "#ffffff",
            QPalette.ColorRole.ToolTipBase: "#161b22",
            QPalette.ColorRole.ToolTipText: "#e6edf3",
            QPalette.ColorRole.PlaceholderText: "#8b949e",
            QPalette.ColorRole.Link: "#58a6ff",
            QPalette.ColorRole.Mid: "#30363d",
            QPalette.ColorRole.Dark: "#010409",
        }
        disabled = "#6e7681"
    else:
        roles = {
            QPalette.ColorRole.Window: "#f6f8fa",
            QPalette.ColorRole.WindowText: "#1f2328",
            QPalette.ColorRole.Base: "#ffffff",
            QPalette.ColorRole.AlternateBase: "#f6f8fa",
            QPalette.ColorRole.Text: "#1f2328",
            QPalette.ColorRole.Button: "#f6f8fa",
            QPalette.ColorRole.ButtonText: "#1f2328",
            QPalette.ColorRole.Highlight: "#0969da",
            QPalette.ColorRole.HighlightedText: "#ffffff",
            QPalette.ColorRole.ToolTipBase: "#ffffff",
            QPalette.ColorRole.ToolTipText: "#1f2328",
            QPalette.ColorRole.PlaceholderText: "#6e7781",
            QPalette.ColorRole.Link: "#0969da",
            QPalette.ColorRole.Mid: "#d0d7de",
            QPalette.ColorRole.Dark: "#afb8c1",
        }
        disabled = "#8c959f"
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(disabled))
    return p


def apply(new_mode: str = "system") -> None:
    """Switch theme and refresh every widget that paints theme colors itself."""
    global _mode
    _mode = new_mode if new_mode in MODES else "system"
    app = QApplication.instance()
    if app is None:
        return
    app.setStyle("Fusion")  # renders the same on every platform and honours the palette fully
    app.setPalette(_palette(is_dark()))
    for widget in app.allWidgets():
        refresh = getattr(widget, "apply_theme", None)
        if callable(refresh):
            refresh()


def follow_system_changes() -> None:
    """Re-apply when the OS switches light/dark, if the user chose "system"."""
    try:
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _scheme: _mode == "system" and apply("system"))
    except AttributeError:
        pass
