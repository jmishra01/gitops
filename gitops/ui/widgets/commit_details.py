"""Commit details: message, author, actions, the files it changed, and the selected file's diff."""

from __future__ import annotations

from pathlib import PurePosixPath

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gitops.core.encoding import display_text
from gitops.core.git_backend import GitBackend, GitError
from gitops.core.models import Commit, CommitFile
from gitops.core.patch import parse_file_patch
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui.theme import colors
from gitops.ui.widgets.common import avatar_pixmap, relative_time
from gitops.ui.widgets.diff_view import DiffView
from gitops.ui.widgets.file_list import CHIPS
from gitops.ui.widgets.hunk_view import HunkDiffView

FILE_ROLE = Qt.ItemDataRole.UserRole + 20
BARS = 5


def change_bars(added: int | None, removed: int | None) -> list[str]:
    """Five blocks split green/red by share of added vs removed lines ("binary" gets grey)."""
    if added is None and removed is None:
        return ["neutral"] * BARS
    a, r = added or 0, removed or 0
    total = a + r
    if total == 0:
        return ["neutral"] * BARS
    green = round(BARS * a / total)
    red = BARS - green
    return ["add"] * green + ["del"] * red


class CommitFileDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 34)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        f: CommitFile = index.data(FILE_ROLE)
        c = colors()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(4, 1, -4, -1)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QColor(c.accent_soft_border))
            painter.setBrush(QColor(c.accent_soft))
            painter.drawRoundedRect(r, 8, 8)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(c.field_bg))
            painter.drawRoundedRect(r, 8, 8)

        chip_text, _tip, (bg, fg) = CHIPS.get(f.status, (f.status, "", ("field_bg", "text")))
        chip = QRect(r.left() + 10, r.center().y() - 9, 18, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(getattr(c, bg)))
        painter.drawRoundedRect(chip, 4, 4)
        bold = QFont(option.font)
        bold.setBold(True)
        bold.setPointSizeF(max(option.font.pointSizeF() - 2, 8))
        painter.setFont(bold)
        painter.setPen(QColor(getattr(c, fg)))
        painter.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), chip_text)

        # Right side: +a -d and the bars
        right = r.right() - 10
        bar_w, gap = 6, 2
        bars_x = right - BARS * bar_w - (BARS - 1) * gap
        for n, kind in enumerate(change_bars(f.added, f.removed)):
            color = {"add": c.add_fg, "del": c.del_fg}.get(kind, c.border_strong)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRect(bars_x + n * (bar_w + gap), r.center().y() - 5, bar_w, 10), 2, 2)
        right = bars_x - 10
        mono = QFont(option.font)
        mono.setFamilies(["JetBrains Mono", "Menlo", "Consolas", "monospace"])
        mono.setPointSizeF(max(option.font.pointSizeF() - 2, 8))
        painter.setFont(mono)
        fm = painter.fontMetrics()
        mid = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        if f.added is None and f.removed is None:
            painter.setPen(QColor(c.text_secondary))
            painter.drawText(QRect(right - 50, r.top(), 50, r.height()), mid, "binary")
            right -= 58
        else:
            painter.setPen(QColor(c.del_fg))
            painter.drawText(QRect(right - 34, r.top(), 34, r.height()), mid, f"−{f.removed or 0}")
            painter.setPen(QColor(c.add_fg))
            painter.drawText(QRect(right - 74, r.top(), 36, r.height()), mid, f"+{f.added or 0}")
            right -= 82

        path = PurePosixPath(f.path)
        name = path.name if not f.old_path else f"{PurePosixPath(f.old_path).name} → {path.name}"
        folder = "" if str(path.parent) == "." else f"{path.parent}/"
        left = chip.right() + 10
        avail = max(right - left, 10)
        name_font = QFont(option.font)
        name_font.setWeight(QFont.Weight.Medium)
        painter.setFont(name_font)
        fm = painter.fontMetrics()
        shown = fm.elidedText(name, Qt.TextElideMode.ElideMiddle, avail)
        painter.setPen(QColor(c.text))
        painter.drawText(QRect(left, r.top(), avail, r.height()), int(Qt.AlignmentFlag.AlignVCenter), shown)
        used = fm.horizontalAdvance(shown) + 6
        if folder and used + 20 < avail:
            painter.setFont(option.font)
            folder_shown = painter.fontMetrics().elidedText(folder, Qt.TextElideMode.ElideLeft, avail - used)
            painter.setPen(QColor(c.text_secondary))
            painter.drawText(
                QRect(left + used, r.top(), avail - used, r.height()), int(Qt.AlignmentFlag.AlignVCenter), folder_shown
            )
        painter.restore()


class CommitDetails(QFrame):
    """Right-hand panel of History. Emits intents; the window performs the git actions."""

    action_requested = Signal(str, object)  # (action, Commit); see ACTIONS
    open_file_history = Signal(str)
    open_blame = Signal(str, object)  # (path, rev)

    ACTIONS = (
        "checkout",
        "cherry_pick",
        "revert",
        "new_branch",
        "new_tag",
        "reset_soft",
        "reset_mixed",
        "reset_hard",
        "rebase_from",
    )

    def __init__(self, runner: TaskRunner, parent=None):
        super().__init__(parent, objectName="Panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.runner = runner
        self.backend: GitBackend | None = None
        self.commit: Commit | None = None
        self.files: list[CommitFile] = []
        self._generation = 0

        # ---- header
        self.subject = QLabel()
        self.subject.setWordWrap(True)
        self.subject.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.subject.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.body = QLabel(objectName="Muted")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setMaximumHeight(120)
        self.avatar = QLabel()
        self.byline = QLabel()
        self.byline.setTextFormat(Qt.TextFormat.RichText)
        self.sha_button = QToolButton()
        self.sha_button.setProperty("variant", "pill")
        self.sha_button.setToolTip("Copy the full SHA")
        self.sha_button.clicked.connect(self._copy_sha)
        self.parents = QLabel(objectName="Muted")
        meta = QHBoxLayout()
        meta.setSpacing(8)
        meta.addWidget(self.avatar)
        meta.addWidget(self.byline)
        meta.addWidget(self.sha_button)
        meta.addWidget(self.parents)
        meta.addStretch(1)

        self.checkout_btn = self._button(
            "Check out", "checkout", "Look at this commit (detached HEAD); no branch moves"
        )
        self.pick_btn = self._button("Cherry-pick", "cherry_pick", "Copy this commit onto your current branch")
        self.revert_btn = self._button("Revert", "revert", "Make a new commit that undoes this one")
        self.here_btn = QToolButton(text="Branch or tag here  ▾")
        self.here_btn.setProperty("variant", "secondary")
        self.here_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        here_menu = QMenu(self.here_btn)
        here_menu.addAction("New branch here…", lambda: self._emit("new_branch"))
        here_menu.addAction("New tag here…", lambda: self._emit("new_tag"))
        self.here_btn.setMenu(here_menu)
        self.more_btn = QToolButton(text="···")
        self.more_btn.setProperty("variant", "secondary")
        self.more_btn.setAccessibleName("More actions: reset, edit history, copy")
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more = QMenu(self.more_btn)
        reset = more.addMenu("Reset current branch to here")
        reset.addAction("Soft: keep changes staged", lambda: self._emit("reset_soft"))
        reset.addAction("Mixed: keep changes unstaged", lambda: self._emit("reset_mixed"))
        reset.addAction("Hard: discard all changes…", lambda: self._emit("reset_hard"))
        more.addAction("Edit commits after this one…", lambda: self._emit("rebase_from"))
        more.addSeparator()
        more.addAction("Copy SHA", self._copy_sha)
        more.addAction(
            "Copy message",
            lambda: self.commit
            and QGuiApplication.clipboard().setText(
                self.subject.text() + ("\n\n" + self.body.text() if self.body.text() else "")
            ),
        )
        self.more_btn.setMenu(more)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        for w in (self.checkout_btn, self.pick_btn, self.revert_btn, self.here_btn, self.more_btn):
            actions.addWidget(w)
        actions.addStretch(1)

        header = QWidget()
        head = QVBoxLayout(header)
        head.setContentsMargins(24, 20, 24, 14)
        head.setSpacing(10)
        head.addWidget(self.subject)
        head.addWidget(self.body)
        head.addLayout(meta)
        head.addLayout(actions)
        divider = QFrame(objectName="PanelDivider")
        divider.setFixedHeight(1)

        # ---- changed files
        self.files_label = QLabel(objectName="SectionLabel")
        self.totals = QLabel()
        self.totals.setTextFormat(Qt.TextFormat.RichText)
        files_head = QHBoxLayout()
        files_head.setContentsMargins(24, 12, 24, 4)
        files_head.addWidget(self.files_label)
        files_head.addStretch(1)
        files_head.addWidget(self.totals)
        self.file_model = QStandardItemModel(self)
        self.file_list = QListView()
        self.file_list.setModel(self.file_model)
        self.file_list.setItemDelegate(CommitFileDelegate(self.file_list))
        self.file_list.setMouseTracking(True)
        self.file_list.setFrameShape(QFrame.Shape.NoFrame)
        self.file_list.setStyleSheet("QListView { background: transparent; outline: 0; }")
        self.file_list.selectionModel().currentChanged.connect(self._file_selected)
        self.file_list.doubleClicked.connect(lambda idx: self.open_file_history.emit(idx.data(FILE_ROLE).path))
        files_box = QWidget()
        files_layout = QVBoxLayout(files_box)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(0)
        files_layout.addLayout(files_head)
        list_wrap = QHBoxLayout()
        list_wrap.setContentsMargins(12, 0, 12, 0)
        list_wrap.addWidget(self.file_list)
        files_layout.addLayout(list_wrap, 1)

        # ---- selected file's diff
        self.diff_name = QLabel(objectName="FileTitle")
        self.inspect_btn = QPushButton("Open in file inspector")
        self.inspect_btn.setProperty("variant", "secondary")
        self.inspect_btn.clicked.connect(
            lambda: self._current_file() and self.open_file_history.emit(self._current_file().path)
        )
        self.blame_btn = QPushButton("Blame")
        self.blame_btn.setProperty("variant", "secondary")
        self.blame_btn.clicked.connect(self._blame_current)
        diff_head = QFrame(objectName="DiffHeader")
        diff_head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        dh = QHBoxLayout(diff_head)
        dh.setContentsMargins(16, 8, 12, 8)
        dh.addWidget(self.diff_name, 1)
        dh.addWidget(self.blame_btn)
        dh.addWidget(self.inspect_btn)
        self.hunks = HunkDiffView()
        self.text = DiffView()
        self.diff_stack = QStackedWidget()
        self.diff_stack.addWidget(self.hunks)
        self.diff_stack.addWidget(self.text)
        diff_box = QWidget()
        db = QVBoxLayout(diff_box)
        db.setContentsMargins(0, 0, 0, 0)
        db.setSpacing(0)
        db.addWidget(diff_head)
        db.addWidget(self.diff_stack, 1)

        split = QSplitter(Qt.Orientation.Vertical, objectName="history_details_split")
        split.addWidget(files_box)
        split.addWidget(diff_box)
        split.setSizes([220, 420])

        self.empty = QLabel("Select a commit to see what it changed", objectName="EmptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content = QWidget()
        cl = QVBoxLayout(self.content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(header)
        cl.addWidget(divider)
        cl.addWidget(split, 1)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.empty)
        self.pages.addWidget(self.content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.pages)

    def _button(self, text: str, action: str, tip: str) -> QPushButton:
        b = QPushButton(text)
        b.setProperty("variant", "secondary")
        b.setToolTip(tip)
        b.clicked.connect(lambda: self._emit(action))
        return b

    def _emit(self, action: str) -> None:
        if self.commit:
            self.action_requested.emit(action, self.commit)

    def _copy_sha(self) -> None:
        if self.commit:
            QGuiApplication.clipboard().setText(self.commit.sha)
            window = self.window()
            if hasattr(window, "statusBar"):
                window.statusBar().showMessage(f"Copied {self.commit.sha}", 3000)

    # ------------------------------------------------------------------ content

    def set_backend(self, backend: GitBackend | None) -> None:
        self.backend = backend
        self.clear()

    def clear(self) -> None:
        self.commit = None
        self.files = []
        self.file_model.clear()
        self.pages.setCurrentWidget(self.empty)

    def show_commit(self, commit: Commit) -> None:
        if not self.backend:
            return
        self.commit = commit
        self._generation += 1
        generation, backend = self._generation, self.backend
        self.pages.setCurrentWidget(self.content)
        self.subject.setText(commit.subject)
        self.body.clear()
        self.body.hide()
        self.avatar.setPixmap(avatar_pixmap(commit.author, 22, commit.email))
        self._render_byline()
        self.byline.setToolTip(f"{commit.email}\n{commit.date:%Y-%m-%d %H:%M}")
        self.sha_button.setText(f"{commit.short_sha}  ⧉")
        self.parents.setText(f"{len(commit.parents)} parents" if commit.is_merge else "")
        self.parents.setToolTip("Changes below are compared with the first parent" if commit.is_merge else "")
        self.files_label.setText("Loading files…")
        self.totals.clear()
        self.file_model.clear()
        self.text.setPlainText("")
        self.diff_stack.setCurrentWidget(self.text)
        self.diff_name.clear()

        def load():
            return generation, backend.commit_message(commit.sha), backend.commit_files(commit)

        task = Task(load)
        task.signals.result.connect(self._loaded)
        task.signals.error.connect(lambda msg: self.files_label.setText(f"Couldn't load this commit: {msg}"))
        self.runner.submit(task)

    def _loaded(self, result) -> None:
        generation, message, files = result
        if generation != self._generation:
            return
        body = message.split("\n", 1)[1].strip() if "\n" in message else ""
        self.body.setText(body)
        self.body.setVisible(bool(body))
        self.files = files
        n = len(files)
        self.files_label.setText(
            f"{n} FILE{'S' if n != 1 else ''} CHANGED"
            + (" · VS FIRST PARENT" if self.commit and self.commit.is_merge else "")
        )
        self._render_totals()
        for f in files:
            it = QStandardItem()
            it.setData(f, FILE_ROLE)
            it.setToolTip(f"{f.old_path} → {f.path}" if f.old_path else f.path)
            it.setEditable(False)
            self.file_model.appendRow(it)
        if files:
            self.file_list.setCurrentIndex(self.file_model.index(0, 0))
        else:
            self.text.setPlainText(
                "This commit changes no files (an empty commit, or a merge that took one side as is)."
            )

    # Rich text bakes colours in, so these are re-rendered when the theme changes.
    def _render_byline(self) -> None:
        if not self.commit:
            return
        c = colors()
        verb = "merged" if self.commit.is_merge else "committed"
        self.byline.setText(
            f"<span style='color:{c.text}; font-weight:600'>{self.commit.author}</span> "
            f"<span style='color:{c.text_secondary}'>{verb} {relative_time(self.commit.date)}</span>"
        )

    def _render_totals(self) -> None:
        c = colors()
        added = sum(f.added or 0 for f in self.files)
        removed = sum(f.removed or 0 for f in self.files)
        self.totals.setText(
            f"<span style='color:{c.add_fg}; font-family:monospace'>+{added}</span> "
            f"<span style='color:{c.del_fg}; font-family:monospace'>−{removed}</span>"
        )

    def apply_theme(self) -> None:
        self._render_byline()
        if self.files:
            self._render_totals()
        self.file_list.viewport().update()

    def _current_file(self) -> CommitFile | None:
        index = self.file_list.currentIndex()
        return index.data(FILE_ROLE) if index.isValid() else None

    def select_file(self, path: str) -> bool:
        for row in range(self.file_model.rowCount()):
            if self.file_model.item(row).data(FILE_ROLE).path == path:
                self.file_list.setCurrentIndex(self.file_model.index(row, 0))
                return True
        return False

    def _file_selected(self, current: QModelIndex, _previous) -> None:
        if not current.isValid() or not self.commit or not self.backend:
            return
        f: CommitFile = current.data(FILE_ROLE)
        self.diff_name.setText(
            f"{PurePosixPath(f.old_path).name} → {PurePosixPath(f.path).name}"
            if f.old_path
            else PurePosixPath(f.path).name
        )
        self.diff_name.setToolTip(f.path)
        self.blame_btn.setEnabled(f.status != "D")
        try:
            text = self.backend.commit_file_diff(self.commit, f)
        except GitError as exc:
            self.text.show_diff(f"Couldn't show this file: {exc}")
            self.diff_stack.setCurrentWidget(self.text)
            return
        patch = parse_file_patch(text) if len(text) <= DiffView.MAX_CHARS else None
        if patch is not None and patch.can_stage_lines:
            self.hunks.show_patch(patch, None, selectable=False)
            self.diff_stack.setCurrentWidget(self.hunks)
        else:
            shown = display_text(text) if text.strip() else "No line changes (only the file mode or a rename)."
            if patch is not None and patch.binary:
                shown = f"Binary file {f.path} changed"
            self.text.show_diff(shown)
            self.diff_stack.setCurrentWidget(self.text)

    def _blame_current(self) -> None:
        f = self._current_file()
        if f and self.commit:
            self.open_blame.emit(f.path, self.commit.sha)

    def diff_text(self) -> str:
        """What the diff area shows (for tests)."""
        if self.diff_stack.currentWidget() is self.hunks:
            return self.hunks.plain_text()
        return self.text.toPlainText()
