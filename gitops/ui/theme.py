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
    # Workbench surfaces and controls (the redesign)
    window: str
    surface: str
    surface_alt: str
    border: str
    border_strong: str
    text: str
    text_secondary: str
    primary: str  # filled primary buttons (white text on it)
    primary_hover: str
    accent_soft: str  # selected rows, active nav item
    accent_soft_border: str
    accent_on_soft: str
    success_soft: str
    success_on_soft: str
    warning_soft: str
    warning_on_soft: str
    danger_soft: str
    danger_on_soft: str
    danger_border: str
    hunk_header_bg: str
    field_bg: str  # search field
    toast_bg: str
    toast_fg: str


LIGHT = Colors(
    dark=False,
    success="#1a7f37",
    warning="#9a6700",
    danger="#cf222e",
    accent="#2F5BEA",
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
    window="#F6F7F9",
    surface="#FFFFFF",
    surface_alt="#FBFBFC",
    border="#E3E6EA",
    border_strong="#C9CED6",
    text="#16181D",
    text_secondary="#5B6370",
    primary="#2F5BEA",
    primary_hover="#2449C8",
    accent_soft="#EEF2FE",
    accent_soft_border="#C9D5FB",
    accent_on_soft="#1F3FB5",
    success_soft="#E7F6EC",
    success_on_soft="#116329",
    warning_soft="#FFF4D6",
    warning_on_soft="#7A4A00",
    danger_soft="#FDECEA",
    danger_on_soft="#B42318",
    danger_border="#F3C4BF",
    hunk_header_bg="#F6F7FB",
    field_bg="#F1F3F5",
    toast_bg="#16181D",
    toast_fg="#FFFFFF",
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
    window="#0E1116",
    surface="#161B22",
    surface_alt="#12161C",
    border="#262C36",
    border_strong="#3A4250",
    text="#E6EDF3",
    text_secondary="#9AA4B2",
    primary="#3D6DF2",
    primary_hover="#5580F5",
    accent_soft="#1B2A4A",
    accent_soft_border="#2E4A86",
    accent_on_soft="#A8C1FF",
    success_soft="#12301D",
    success_on_soft="#7EE2A0",
    warning_soft="#3A2C0B",
    warning_on_soft="#F2C66D",
    danger_soft="#3B1519",
    danger_on_soft="#FF9B95",
    danger_border="#7A2A2F",
    hunk_header_bg="#1C2128",
    field_bg="#0D1117",
    toast_bg="#E6EDF3",
    toast_fg="#0E1116",
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


def stylesheet(c: Colors) -> str:
    """Styles for the workbench components, selected by objectName or a `variant` property.

    Scoped this way so widgets that don't opt in keep the plain Fusion look.
    """
    return f"""
    #AppBody, #ChangesPage {{ background: {c.window}; }}
    #TopBar {{ background: {c.surface}; border-bottom: 1px solid {c.border}; }}
    #NavRail {{ background: {c.surface}; border-right: 1px solid {c.border}; }}
    #Panel {{ background: {c.surface}; }}
    #PanelDivider {{ background: {c.border}; }}
    #Composer {{ background: {c.surface_alt}; border-top: 1px solid {c.border}; }}
    #DiffHeader {{ background: {c.surface}; border-bottom: 1px solid {c.border}; }}
    #SectionLabel {{ color: {c.text_secondary}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; }}
    #Muted {{ color: {c.text_secondary}; }}
    #Title {{ font-size: 15px; font-weight: 600; color: {c.text}; }}
    #FileTitle {{ font-size: 13px; font-weight: 600; color: {c.text}; }}

    QToolButton#RailButton {{
        border: none; border-radius: 10px; padding: 6px 0px; color: {c.text_secondary};
        font-size: 11px; background: transparent;
    }}
    QToolButton#RailButton:hover {{ background: {c.field_bg}; }}
    QToolButton#RailButton:checked {{ background: {c.accent_soft}; color: {c.accent_on_soft}; font-weight: 600; }}
    QLabel#RailBadge {{
        background: {c.primary}; color: #FFFFFF; border-radius: 9px; font-size: 11px; font-weight: 600;
        padding: 0px 5px;
    }}

    QPushButton[variant="primary"] {{
        background: {c.primary}; color: #FFFFFF; border: none; border-radius: 8px;
        padding: 0px 14px; min-height: 34px; font-weight: 600;
    }}
    QPushButton[variant="primary"]:hover {{ background: {c.primary_hover}; }}
    QPushButton[variant="primary"]:disabled {{ background: {c.border_strong}; color: {c.surface}; }}
    QPushButton[variant="secondary"], QToolButton[variant="secondary"] {{
        background: {c.surface}; color: {c.text}; border: 1px solid {c.border_strong}; border-radius: 8px;
        padding: 0px 12px; min-height: 30px; font-weight: 500;
    }}
    QPushButton[variant="secondary"]:hover, QToolButton[variant="secondary"]:hover {{ background: {c.field_bg}; }}
    QPushButton[variant="danger"] {{
        background: {c.surface}; color: {c.danger_on_soft}; border: 1px solid {c.danger_border}; border-radius: 8px;
        padding: 0px 12px; min-height: 30px; font-weight: 500;
    }}
    QPushButton[variant="danger"]:hover {{ background: {c.danger_soft}; }}
    QToolButton[variant="ghost"] {{
        background: transparent; color: {c.text_secondary}; border: none; border-radius: 8px; padding: 6px;
    }}
    QToolButton[variant="ghost"]:hover {{ background: {c.field_bg}; color: {c.text}; }}
    QToolButton[variant="ghost"]::menu-indicator {{ image: none; width: 0px; }}
    QToolButton[variant="ghost"]:checked {{ background: {c.accent_soft}; }}
    QToolButton[variant="pill"] {{
        background: {c.surface}; color: {c.text}; border: 1px solid {c.border}; border-radius: 8px;
        padding: 0px 10px; min-height: 32px;
    }}
    QToolButton[variant="pill"]:hover {{ background: {c.field_bg}; }}
    QToolButton[variant="pill"]::menu-indicator {{ image: none; width: 0px; }}
    QToolButton[variant="pill"]::menu-button {{
        border: none; border-left: 1px solid {c.border}; width: 22px;
        border-top-right-radius: 8px; border-bottom-right-radius: 8px;
    }}
    QToolButton[variant="sync"] {{
        background: {c.primary}; color: #FFFFFF; border: none; border-radius: 8px;
        padding: 0px 14px; min-height: 34px; font-weight: 600;
    }}
    QToolButton[variant="sync"]:hover {{ background: {c.primary_hover}; }}
    QToolButton[variant="sync"]::menu-button {{ border-left: 1px solid rgba(255,255,255,0.35); width: 26px; }}

    #Segmented {{ background: {c.field_bg}; border-radius: 8px; }}
    QToolButton[variant="secondary"]::menu-indicator {{ image: none; width: 0px; }}
    QTreeWidget#Sidebar {{
        background: {c.surface}; border: none; border-right: 1px solid {c.border}; padding: 8px 4px; color: {c.text};
    }}
    QTreeWidget#Sidebar::item {{ min-height: 26px; border-radius: 6px; padding-left: 2px; }}
    QTreeWidget#Sidebar::item:hover {{ background: {c.field_bg}; }}
    QTreeWidget#Sidebar::item:selected {{ background: {c.accent_soft}; color: {c.accent_on_soft}; }}
    QPushButton[variant="segment"] {{
        background: transparent; color: {c.text_secondary}; border: none; border-radius: 6px;
        padding: 0px 10px; min-height: 28px;
    }}
    QPushButton[variant="segment"]:checked {{
        background: {c.surface}; color: {c.text}; font-weight: 600; border: 1px solid {c.border};
    }}
    QTableView#CommitTable {{ background: {c.surface}; border: none; }}
    QLineEdit#SearchField {{
        background: {c.field_bg}; border: 1px solid transparent; border-radius: 8px; padding: 0px 10px;
        min-height: 32px; color: {c.text};
    }}
    QLineEdit#SearchField:focus {{ border: 1px solid {c.accent_soft_border}; background: {c.surface}; }}
    #Composer QLineEdit, #Composer QPlainTextEdit {{
        background: {c.surface}; border: 1px solid {c.border_strong}; border-radius: 8px; padding: 6px 8px;
        color: {c.text};
    }}
    #Composer QLineEdit {{ min-height: 22px; }}
    #Composer QLineEdit:focus, #Composer QPlainTextEdit:focus {{ border: 1px solid {c.primary}; }}

    #HunkCard {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 10px; }}
    #HunkHeader {{ background: {c.hunk_header_bg}; border-bottom: 1px solid {c.border};
        border-top-left-radius: 10px; border-top-right-radius: 10px; }}
    #HunkHeaderText {{ color: {c.hunk}; }}
    #DiffScroll, #DiffCanvas {{ background: {c.window}; border: none; }}
    #EmptyState {{ color: {c.text_secondary}; font-size: 14px; }}
    #SelectionBar {{ background: {c.toast_bg}; border-radius: 12px; }}
    #SelectionBar QLabel {{ color: {c.toast_fg}; font-weight: 500; }}
    #SelectionBar QPushButton {{
        background: transparent; color: {c.toast_fg}; border: 1px solid {c.text_secondary}; border-radius: 8px;
        padding: 0px 12px; min-height: 30px;
    }}
    #SelectionBar QPushButton[variant="primary"] {{ background: {c.primary}; color: #FFFFFF; border: none; }}
    #Banner {{ background: {c.banner_bg}; border: 1px solid {c.banner_border}; border-radius: 8px; }}
    #Banner QLabel {{ color: {c.banner_fg}; border: none; background: transparent; }}
    """


def apply(new_mode: str = "system") -> None:
    """Switch theme and refresh every widget that paints theme colors itself."""
    global _mode
    _mode = new_mode if new_mode in MODES else "system"
    app = QApplication.instance()
    if app is None:
        return
    app.setStyle("Fusion")  # renders the same on every platform and honours the palette fully
    app.setPalette(_palette(is_dark()))
    app.setStyleSheet(stylesheet(colors()))
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
