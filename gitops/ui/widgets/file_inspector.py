"""File inspector: one file's whole story. History with a timeline and A/B compare, and blame.

Opened from any file (Changes, History, Search) or with the file picker in its header.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QModelIndex, QRect, QSize, QStringListModel, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gitops.core.compare import SideBySideRow, align, is_binary, lines_of
from gitops.core.file_history import file_history, version_content, working_copy
from gitops.core.git_backend import GitBackend
from gitops.core.models import ChangeKind, FileHistoryEntry
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui.theme import colors
from gitops.ui.widgets.blame_view import BlameView
from gitops.ui.widgets.common import relative_time
from gitops.ui.widgets.file_browser import FileBrowser
from gitops.ui.widgets.hunk_view import mono_font
from gitops.ui.widgets.timeline import Mark, TimelineScrubber

VERSION_ROLE = Qt.ItemDataRole.UserRole + 30
ROW_H = 22
NUM_W = 46
MAX_COMPARE_LINES = 20000


@dataclass(frozen=True)
class Version:
    entry: FileHistoryEntry | None  # None: the working copy
    path: str
    subject: str
    kind: str  # display label: Working copy, Created, Modified, Renamed, Deleted
    author: str
    when: datetime
    delta: str

    @property
    def is_working(self) -> bool:
        return self.entry is None

    @property
    def short(self) -> str:
        return "Working copy" if self.entry is None else self.entry.commit.short_sha

    @property
    def mark_kind(self) -> str:
        return "working" if self.entry is None else self.kind.lower()


def build_versions(backend: GitBackend, path: str, entries: list[FileHistoryEntry]) -> list[Version]:
    """Working copy first, then every commit that touched the file, newest first."""
    current = working_copy(backend, path)
    latest = version_content(backend, entries[0]) if entries else None
    if current is None:
        subject = "Deleted in the working copy" if entries else "Not in the working copy"
    elif current == latest:
        subject = "Same as the last commit"
    else:
        subject = "Uncommitted changes"
    versions = [Version(None, path, subject, "Working copy", "You", datetime.now(timezone.utc).astimezone(), "")]
    for e in entries:
        if e.added is None:
            delta = "binary"
        elif e.kind is ChangeKind.RENAMED and not e.added and not e.removed:
            delta = "±0"
        else:
            delta = f"+{e.added}" + (f" −{e.removed}" if e.removed else "")
        versions.append(Version(e, e.path, e.commit.subject, e.kind.value, e.commit.author, e.commit.date, delta))
    return versions


def version_bytes(backend: GitBackend, version: Version) -> bytes | None:
    return working_copy(backend, version.path) if version.entry is None else version_content(backend, version.entry)


# ---------------------------------------------------------------------- side-by-side compare


class SplitCanvas(QWidget):
    def __init__(self, view: SplitDiffView):
        super().__init__()
        self.view = view
        self.font_ = mono_font(self.font())
        self.setMouseTracking(False)

    def sizeHint(self) -> QSize:
        return QSize(800, max(len(self.view.rows), 1) * ROW_H)

    def paintEvent(self, event) -> None:
        c = colors()
        p = QPainter(self)
        p.setFont(self.font_)
        rows = self.view.rows
        half = self.width() // 2
        first = max(event.rect().top() // ROW_H, 0)
        last = min(event.rect().bottom() // ROW_H + 1, len(rows))
        filler = QColor(c.field_bg)
        for i in range(first, last):
            row: SideBySideRow = rows[i]
            top = i * ROW_H
            for side in (0, 1):
                x0 = side * half
                rect = QRect(x0, top, half, ROW_H)
                number = row.left_no if side == 0 else row.right_no
                text = row.left if side == 0 else row.right
                if number is None:
                    p.fillRect(rect, filler)
                    continue
                if row.kind in ("replace", "delete") and side == 0:
                    p.fillRect(rect, QColor(c.del_bg))
                elif row.kind in ("replace", "insert") and side == 1:
                    p.fillRect(rect, QColor(c.add_bg))
                p.setPen(QColor(c.text_secondary))
                p.drawText(QRect(x0, top, NUM_W - 10, ROW_H), int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), str(number))
                p.setPen(QColor(c.text))
                p.drawText(QRect(x0 + NUM_W, top, half - NUM_W - 6, ROW_H), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text.replace("\t", "    "))
        p.setPen(QPen(QColor(c.border), 1))
        p.drawLine(half, event.rect().top(), half, event.rect().bottom())
        p.end()


class SplitDiffView(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[SideBySideRow] = []
        self.canvas = SplitCanvas(self)
        self.setWidget(self.canvas)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_rows(self, rows: list[SideBySideRow]) -> None:
        self.rows = rows
        self.canvas.setMinimumHeight(max(len(rows), 1) * ROW_H)
        self.canvas.updateGeometry()
        self.canvas.update()

    def first_change(self) -> int | None:
        return next((i for i, r in enumerate(self.rows) if r.kind != "equal"), None)

    def scroll_to_first_change(self) -> None:
        i = self.first_change()
        if i is not None:
            self.verticalScrollBar().setValue(max(i * ROW_H - 3 * ROW_H, 0))


# ---------------------------------------------------------------------- versions list


class VersionDelegate(QStyledItemDelegate):
    def __init__(self, inspector: FileInspector):
        super().__init__(inspector)
        self.inspector = inspector

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 58)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        v: Version = index.data(VERSION_ROLE)
        c = colors()
        row = index.row()
        is_a, is_b = row == self.inspector.a, row == self.inspector.b
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(6, 2, -6, -2)
        if is_a:
            painter.setPen(QPen(QColor(c.accent_soft_border), 1))
            painter.setBrush(QColor(c.accent_soft))
            painter.drawRoundedRect(r, 10, 10)
        elif is_b:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c.field_bg))
            painter.drawRoundedRect(r, 10, 10)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c.window))
            painter.drawRoundedRect(r, 10, 10)

        dot = QRect(r.left() + 12, r.top() + 10, 22, 22)
        kind_color = {"created": c.add_fg, "deleted": c.del_fg, "renamed": c.hunk, "working": c.text}.get(v.mark_kind, c.text_secondary)
        fill = c.primary if is_a else (c.text if is_b else kind_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(fill))
        if is_a or is_b:
            painter.drawEllipse(dot)
            bold = QFont(option.font)
            bold.setBold(True)
            bold.setPixelSize(11)
            painter.setFont(bold)
            painter.setPen(QColor("#FFFFFF" if is_a else c.surface))
            painter.drawText(dot, int(Qt.AlignmentFlag.AlignCenter), "A" if is_a else "B")
        else:
            painter.drawEllipse(dot.adjusted(6, 6, -6, -6))

        right = r.right() - 12
        mono = mono_font(option.font)
        mono.setPixelSize(11)
        painter.setFont(mono)
        if v.delta:
            # Measure with the painter's own metrics (it may substitute glyphs such as "−"), and never clip.
            w = painter.fontMetrics().horizontalAdvance(v.delta) + 4
            painter.setPen(QColor(c.text_secondary))
            flags = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight | Qt.TextFlag.TextDontClip
            painter.drawText(QRect(right - w, r.top() + 8, w, 20), int(flags), v.delta)
            right -= w + 10
        left = dot.right() + 12
        title = QFont(option.font)
        title.setWeight(QFont.Weight.DemiBold if is_a else QFont.Weight.Medium)
        painter.setFont(title)
        painter.setPen(QColor(c.text))
        tw = right - left
        painter.drawText(QRect(left, r.top() + 8, tw, 20), int(Qt.AlignmentFlag.AlignVCenter), painter.fontMetrics().elidedText(v.subject, Qt.TextElideMode.ElideRight, tw))
        painter.setFont(option.font)
        painter.setPen(QColor(c.text_secondary))
        when = "now" if v.is_working else relative_time(v.when)
        meta = f"{v.kind} · {v.author} · {when}" + (f" · {v.short}" if not v.is_working else "")
        width = r.right() - 12 - left
        painter.drawText(QRect(left, r.top() + 29, width, 18), int(Qt.AlignmentFlag.AlignVCenter), painter.fontMetrics().elidedText(meta, Qt.TextElideMode.ElideRight, width))
        painter.restore()


class VersionList(QListView):
    picked_a = Signal(int)
    picked_b = Signal(int)
    menu_requested = Signal(int, object)

    def mousePressEvent(self, event) -> None:
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and event.button() == Qt.MouseButton.LeftButton:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.picked_b.emit(index.row())
            else:
                self.picked_a.emit(index.row())
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        index = self.indexAt(event.pos())
        if index.isValid():
            self.menu_requested.emit(index.row(), event.globalPos())


# ---------------------------------------------------------------------- the page


class FileInspector(QWidget):
    back_requested = Signal()
    open_commit = Signal(str)  # sha, shown in History
    search_requested = Signal(str)  # path: search text across this file's history
    changed = Signal()  # the working copy was modified (restore)

    def __init__(self, runner: TaskRunner, parent=None):
        super().__init__(parent, objectName="ChangesPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.runner = runner
        self.backend: GitBackend | None = None
        self.path = ""
        self.versions: list[Version] = []
        self.entries: list[FileHistoryEntry] = []
        self.a: int | None = None
        self.b: int | None = None
        self._generation = 0
        self._compare_generation = 0

        # ---- header
        self.back_btn = QPushButton("‹  Back")
        self.back_btn.setProperty("variant", "secondary")
        self.back_btn.clicked.connect(self.back_requested.emit)
        self.title = QLabel(objectName="Title")
        self.subtitle = QLabel(objectName="Muted")
        self.subtitle.hide()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        self.picker = QLineEdit(objectName="SearchField")
        self.picker.setPlaceholderText("Open another file…")
        self.picker.setToolTip("Type part of a file name or path (Ctrl+P)")
        self.picker.setAccessibleName("File to inspect")
        self.picker.setClearButtonEnabled(True)
        self.picker.setMinimumWidth(240)
        self.picker.setMaximumWidth(420)
        self.picker.hide()  # the empty page has its own, larger finder
        self.completer = QCompleter(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setModel(QStringListModel(self))
        self.picker.setCompleter(self.completer)
        self.picker.returnPressed.connect(lambda: self.open_file(self.picker.text().strip()))
        self.completer.activated.connect(lambda text: self.open_file(text))

        # Which page is showing ("history" or "blame"); the nav rail switches between them.
        self.tab = "history"

        self.options = QToolButton(text="···")
        self.options.setProperty("variant", "secondary")
        self.options.setAccessibleName("File options")
        self.options.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        options_menu = QMenu(self.options)
        self.search_act = options_menu.addAction("Search this file's history…")
        self.search_act.setToolTip("Find text in every past version of this file")
        self.search_act.setEnabled(False)
        self.search_act.triggered.connect(lambda: self.path and self.search_requested.emit(self.path))
        options_menu.addSeparator()
        self.follow_act = options_menu.addAction("Follow renames")
        self.follow_act.setCheckable(True)
        self.follow_act.setChecked(True)
        self.all_refs_act = options_menu.addAction("Include all branches")
        self.all_refs_act.setCheckable(True)
        for act in (self.follow_act, self.all_refs_act):
            act.toggled.connect(lambda _on: self.path and self.show_file(self.path, tab=self.tab))
        self.options.setMenu(options_menu)

        header = QFrame(objectName="DiffHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 10, 16, 10)
        hl.setSpacing(12)
        hl.addWidget(self.back_btn)
        hl.addLayout(titles, 1)
        hl.addWidget(self.picker, 1)
        hl.addWidget(self.options)

        # ---- History tab: timeline, versions, compare
        self.summary = QLabel(objectName="Muted")
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        hint = QLabel("Click a version for A · Shift-click for B · arrow keys step", objectName="Muted")
        self.timeline = TimelineScrubber()
        self.timeline.a_changed.connect(self.set_a)
        self.timeline.b_changed.connect(self.set_b)
        tl_top = QHBoxLayout()
        tl_top.addWidget(self.summary, 1)
        tl_top.addWidget(hint)
        timeline_box = QFrame(objectName="DiffHeader")
        timeline_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tb = QVBoxLayout(timeline_box)
        tb.setContentsMargins(24, 12, 24, 6)
        tb.setSpacing(6)
        tb.addLayout(tl_top)
        tb.addWidget(self.timeline)

        self.model = QStandardItemModel(self)
        self.list = VersionList()
        self.list.setModel(self.model)
        self.list.setItemDelegate(VersionDelegate(self))
        self.list.setMouseTracking(True)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setStyleSheet("QListView { background: transparent; outline: 0; }")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.picked_a.connect(self.set_a)
        self.list.picked_b.connect(self.set_b)
        self.list.menu_requested.connect(self._version_menu)
        list_panel = QFrame(objectName="Panel")
        list_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        list_panel.setMinimumWidth(300)
        lp = QVBoxLayout(list_panel)
        lp.setContentsMargins(6, 8, 6, 8)
        lp.addWidget(self.list)

        self.chip_a = QLabel()
        self.chip_b = QLabel()
        arrow = QLabel("→", objectName="Muted")
        self.swap_btn = QPushButton("⇄")
        self.swap_btn.setProperty("variant", "secondary")
        self.swap_btn.setAccessibleName("Swap A and B")
        self.swap_btn.setFixedWidth(36)
        self.swap_btn.clicked.connect(self.swap)
        self.open_btn = QPushButton("Open version A")
        self.save_btn = QPushButton("Save a copy…")
        self.restore_btn = QPushButton("Restore version A…")
        for b in (self.open_btn, self.save_btn, self.restore_btn):
            b.setProperty("variant", "secondary")
        self.open_btn.clicked.connect(lambda: self.a is not None and self.open_version(self.a))
        self.save_btn.clicked.connect(lambda: self.a is not None and self.save_version(self.a))
        self.restore_btn.clicked.connect(lambda: self.a is not None and self.restore_version(self.a))
        bar = QHBoxLayout()
        bar.setSpacing(10)
        for w in (self.chip_a, arrow, self.chip_b, self.swap_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        for w in (self.open_btn, self.save_btn, self.restore_btn):
            bar.addWidget(w)

        self.head_a = QLabel()
        self.head_b = QLabel()
        heads = QHBoxLayout()
        heads.setContentsMargins(12, 6, 12, 6)
        heads.addWidget(self.head_a, 1)
        heads.addWidget(self.head_b, 1)
        heads_box = QFrame(objectName="HunkHeader")
        heads_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        heads_box.setLayout(heads)
        self.split = SplitDiffView()
        self.compare_note = QLabel(objectName="EmptyState")
        self.compare_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.compare_note.setWordWrap(True)
        self.compare_stack = QStackedWidget()
        self.compare_stack.addWidget(self.split)
        self.compare_stack.addWidget(self.compare_note)
        card = QFrame(objectName="HunkCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(1, 1, 1, 1)
        cl.setSpacing(0)
        cl.addWidget(heads_box)
        cl.addWidget(self.compare_stack, 1)
        compare = QWidget()
        cv = QVBoxLayout(compare)
        cv.setContentsMargins(20, 14, 20, 16)
        cv.setSpacing(12)
        cv.addLayout(bar)
        cv.addWidget(card, 1)

        body = QSplitter(objectName="inspector_split")
        body.addWidget(list_panel)
        body.addWidget(compare)
        body.setStretchFactor(1, 1)
        body.setSizes([380, 900])
        body.setHandleWidth(1)
        history_page = QWidget()
        hp = QVBoxLayout(history_page)
        hp.setContentsMargins(0, 0, 0, 0)
        hp.setSpacing(0)
        hp.addWidget(timeline_box)
        hp.addWidget(body, 1)

        # ---- Blame tab
        self.blame = BlameView(runner)
        self.blame.set_embedded(True)

        self.empty = FileBrowser()
        self.empty.file_chosen.connect(self.open_file)
        find = QShortcut(QKeySequence("Ctrl+P"), self, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find.activated.connect(self.focus_finder)
        self.pages = QStackedWidget()
        for w in (self.empty, history_page, self.blame):
            self.pages.addWidget(w)
        self.history_page = history_page

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.pages, 1)
        self._update_actions()
        self.apply_theme()

    # ------------------------------------------------------------------ setup

    def set_backend(self, backend: GitBackend | None) -> None:
        self.backend = backend
        self.path = ""
        self.versions = []
        self.entries = []
        self.model.clear()
        self.title.setText("File inspector")
        self.subtitle.clear()
        self.subtitle.hide()
        self.picker.hide()
        self.pages.setCurrentWidget(self.empty)
        self.blame.set_backend(backend)
        self.empty.set_files([])
        self.search_act.setEnabled(False)
        if backend:
            task = Task(backend.tracked_files)
            task.signals.result.connect(lambda files, b=backend: b is self.backend and self._set_files(files))
            self.runner.submit(task)

    def _set_files(self, files: list[str]) -> None:
        self.completer.model().setStringList(files)
        self.empty.set_files(files)

    def focus_finder(self) -> None:
        if self.pages.currentWidget() is self.empty:
            self.empty.focus_search()
        else:
            self.picker.setFocus()
            self.picker.selectAll()

    def apply_theme(self) -> None:
        c = colors()
        self.chip_a.setStyleSheet(f"background: {c.accent_soft}; color: {c.accent_on_soft}; border-radius: 8px; padding: 4px 10px; font-weight: 600;")
        self.chip_b.setStyleSheet(f"background: {c.text}; color: {c.surface}; border-radius: 8px; padding: 4px 10px; font-weight: 600;")
        self.head_a.setStyleSheet(f"color: {c.accent_on_soft}; font-weight: 600;")
        self.head_b.setStyleSheet(f"color: {c.text}; font-weight: 600;")
        self.list.viewport().update()
        self.timeline.update()
        self.split.canvas.update()
        self._render_summary()

    # ------------------------------------------------------------------ loading a file

    def show_tab(self, tab: str) -> None:
        if not self.path:
            # No file yet: remember which tab was asked for and let the user pick a file.
            self.tab = tab
            self.title.setText("Blame" if tab == "blame" else "File inspector")
            self.empty.set_purpose(tab)
            self.pages.setCurrentWidget(self.empty)
            self.empty.focus_search()
            return
        self.tab = tab
        if tab == "blame":
            self.pages.setCurrentWidget(self.blame)
            if self.blame.path_edit.text() != self.path or not self.blame.lines:
                self.blame.blame(self.path, None)
        else:
            self.pages.setCurrentWidget(self.history_page)

    def open_file(self, path: str) -> None:
        """Open a file picked in the finder or header, staying on the tab the user is on."""
        if not path:
            return
        tab = self.tab
        self.show_file(path, tab=tab)
        if tab == "blame":
            self.show_tab("blame")

    def show_blame(self, path: str, rev: str | None) -> None:
        if path != self.path:
            self.show_file(path, tab="blame")
        self.tab = "blame"
        self.pages.setCurrentWidget(self.blame)
        self.blame.blame(path, rev)

    def show_file(self, path: str, tab: str = "history") -> None:
        if not self.backend or not path:
            return
        self.path = path.strip().lstrip("/")
        self.picker.clear()
        self.picker.show()
        self.empty.remember(self.path)
        name = PurePosixPath(self.path).name
        self.title.setText(name)
        self.subtitle.setText(self.path)
        self.subtitle.show()
        self.search_act.setEnabled(True)
        self.summary.setText("Loading history…")
        self.model.clear()
        self.versions = []
        self.entries = []
        self.split.set_rows([])
        self._generation += 1
        generation, backend, follow, all_refs = self._generation, self.backend, self.follow_act.isChecked(), self.all_refs_act.isChecked()
        target = self.path

        def load():
            entries = file_history(backend, target, follow=follow, all_refs=all_refs)
            return generation, entries, build_versions(backend, target, entries)

        task = Task(load)
        task.signals.result.connect(self._loaded)
        task.signals.error.connect(lambda msg: self.summary.setText(f"Couldn't load this file's history: {msg}"))
        self.runner.submit(task)
        self.tab = tab
        if tab == "blame":
            self.pages.setCurrentWidget(self.blame)
        else:
            self.pages.setCurrentWidget(self.history_page)

    def _loaded(self, result) -> None:
        generation, entries, versions = result
        if generation != self._generation:
            return
        self.versions = versions
        self.entries = entries
        renames = [e for e in entries if e.kind is ChangeKind.RENAMED and e.old_path]
        if renames:
            self.subtitle.setText(f"{self.path} · was {renames[0].old_path} until {renames[0].commit.date:%b %d, %Y}")
        self._render_summary()
        self.model.clear()
        for v in versions:
            it = QStandardItem()
            it.setData(v, VERSION_ROLE)
            it.setEditable(False)
            it.setToolTip(f"{v.subject}\n{v.path}")
            self.model.appendRow(it)
        marks = [Mark(v.when, v.mark_kind, f"{v.subject}\n{v.kind} · {v.author} · {v.short}") for v in versions]
        # Compare the last commit (A) with the working copy (B) by default: "what have I changed?"
        a = 1 if len(versions) > 1 else 0
        self.a, self.b = a, 0
        self.timeline.set_marks(marks, self.a, self.b)
        self._compare()

    def _render_summary(self) -> None:
        """Rich text bakes colours in, so this is redone when the theme changes."""
        entries = self.entries
        if not entries:
            self.summary.setText("Not committed yet: only the working copy exists." if self.versions else "")
            return
        c = colors()
        renames = [e for e in entries if e.kind is ChangeKind.RENAMED and e.old_path]
        created = entries[-1]
        parts = [
            f"<span style='color:{c.text}; font-weight:600'>{len(entries)} version{'s' if len(entries) != 1 else ''}</span>",
            f"created by {created.commit.author} on {created.commit.date:%b %d, %Y}",
        ]
        if renames:
            parts.append(f"renamed {renames[0].commit.date:%b %d}")
        self.summary.setText(" · ".join(parts))

    # ------------------------------------------------------------------ A / B

    def set_a(self, index: int) -> None:
        if 0 <= index < len(self.versions) and index != self.a:
            self.a = index
            self.timeline.set_a(index, emit=False)
            self._compare()

    def set_b(self, index: int) -> None:
        if 0 <= index < len(self.versions) and index != self.b:
            self.b = index
            self.timeline.set_b(index, emit=False)
            self._compare()

    def swap(self) -> None:
        if self.a is None or self.b is None:
            return
        self.a, self.b = self.b, self.a
        self.timeline.set_marks(self.timeline.marks, self.a, self.b)
        self._compare()

    def _label(self, v: Version) -> str:
        return "Working copy" if v.is_working else f"{v.short} · {v.when:%b %d}"

    def _compare(self) -> None:
        self.list.viewport().update()
        self._update_actions()
        if self.a is None or self.b is None or not self.versions or not self.backend:
            return
        va, vb = self.versions[self.a], self.versions[self.b]
        self.chip_a.setText(f"A · {self._label(va)}")
        self.chip_b.setText(f"B · {self._label(vb)}")
        self.head_a.setText(f"A · {va.path}" + ("" if va.is_working else f" at {va.short}"))
        self.head_b.setText(f"B · {vb.path}" + (", working copy" if vb.is_working else f" at {vb.short}"))
        self._compare_generation += 1
        generation, backend = self._compare_generation, self.backend

        def load():
            old, new = version_bytes(backend, va), version_bytes(backend, vb)
            if (old is not None and is_binary(old)) or (new is not None and is_binary(new)):
                return generation, None, "Binary file: open or save a version to look at it."
            if old == new:
                return generation, None, "A and B are identical."
            a_lines, b_lines = lines_of(old), lines_of(new)
            if max(len(a_lines), len(b_lines)) > MAX_COMPARE_LINES:
                return generation, None, f"Too large to compare side by side (over {MAX_COMPARE_LINES:,} lines)."
            return generation, align(a_lines, b_lines), ""

        task = Task(load)
        task.signals.result.connect(self._compared)
        task.signals.error.connect(lambda msg: self._show_note(f"Couldn't compare: {msg}"))
        self.runner.submit(task)

    def _compared(self, result) -> None:
        generation, rows, note = result
        if generation != self._compare_generation:
            return
        if rows is None:
            self._show_note(note)
            return
        self.split.set_rows(rows)
        self.compare_stack.setCurrentWidget(self.split)
        self.split.scroll_to_first_change()

    def _show_note(self, text: str) -> None:
        self.split.set_rows([])
        self.compare_note.setText(text)
        self.compare_stack.setCurrentWidget(self.compare_note)

    def _update_actions(self) -> None:
        has_a = self.a is not None and bool(self.versions)
        a_working = has_a and self.versions[self.a].is_working
        for b in (self.open_btn, self.save_btn, self.swap_btn):
            b.setEnabled(has_a)
        self.restore_btn.setVisible(has_a and not a_working)

    def compare_text(self) -> str:
        """The compare as plain text, left | right (for tests)."""
        if self.compare_stack.currentWidget() is self.compare_note:
            return self.compare_note.text()
        out = []
        for r in self.split.rows:
            left = f"{r.left_no or '':>4} {r.left}" if r.left_no else "     ~"
            right = f"{r.right_no or '':>4} {r.right}" if r.right_no else "     ~"
            out.append(f"{r.kind[0]} {left} | {right}")
        return "\n".join(out)

    # ------------------------------------------------------------------ version actions

    def _version_menu(self, row: int, global_pos) -> None:
        v = self.versions[row]
        menu = QMenu(self)
        menu.addAction("Compare as A", lambda: self.set_a(row))
        menu.addAction("Compare as B", lambda: self.set_b(row))
        menu.addSeparator()
        menu.addAction("Open", lambda: self.open_version(row))
        menu.addAction("Save a copy…", lambda: self.save_version(row))
        if not v.is_working:
            menu.addAction("Restore this version…", lambda: self.restore_version(row))
            menu.addSeparator()
            if v.entry.kind is not ChangeKind.DELETED:
                menu.addAction("Blame this version", lambda: self.show_blame(v.path, v.entry.commit.sha))
            menu.addAction("Show commit in History", lambda: self.open_commit.emit(v.entry.commit.sha))
        menu.exec(global_pos)

    def open_version(self, row: int) -> None:
        if not self.backend:
            return
        v = self.versions[row]
        data = version_bytes(self.backend, v)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{v.path} · {self._label(v)} (read-only)")
        dialog.resize(820, 620)
        viewer = QPlainTextEdit(readOnly=True)
        viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        viewer.setFont(mono_font(viewer.font()))
        if data is None:
            viewer.setPlainText("The file doesn't exist in this version.")
        elif is_binary(data):
            viewer.setPlainText(f"Binary file, {len(data):,} bytes. Use Save a copy… to get it.")
        else:
            viewer.setPlainText("\n".join(lines_of(data)))
        QVBoxLayout(dialog).addWidget(viewer)
        dialog.show()

    def save_version(self, row: int) -> None:
        if not self.backend:
            return
        v = self.versions[row]
        data = version_bytes(self.backend, v)
        if data is None:
            QMessageBox.information(self, "Save a copy", "The file doesn't exist in this version.")
            return
        name = PurePosixPath(v.path)
        suffix = "working" if v.is_working else v.short
        suggested = str(Path.home() / f"{name.stem}@{suffix}{name.suffix}")
        dest, _ = QFileDialog.getSaveFileName(self, "Save this version as", suggested)
        if dest:
            Path(dest).write_bytes(data)

    def restore_version(self, row: int) -> None:
        if not self.backend:
            return
        v = self.versions[row]
        if v.is_working:
            return
        data = version_bytes(self.backend, v)
        if data is None:
            QMessageBox.information(self, "Restore", "The file doesn't exist in this version, so there's nothing to restore.")
            return
        has_local_edits = bool(self.versions) and self.versions[0].subject == "Uncommitted changes"
        consequence = (
            "Your uncommitted edits to this file will be lost."
            if has_local_edits
            else "The restored content shows up as an uncommitted change you can review before committing."
        )
        answer = QMessageBox.question(
            self,
            "Restore version",
            f"Replace {self.path} in your working copy with the version from {v.short} ({v.when:%b %d, %Y})?\n\n{consequence}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        dest = self.backend.root / self.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        self.changed.emit()
        self.show_file(self.path)
