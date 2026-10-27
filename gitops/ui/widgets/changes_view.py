from __future__ import annotations

from pathlib import PurePosixPath

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gitops.core.git_backend import GitBackend, GitError
from gitops.core.models import FileStatus
from gitops.core.patch import FilePatch, build_partial_patch, parse_file_patch
from gitops.ui.dialogs.conflict_editor import ConflictEditor
from gitops.ui.theme import colors
from gitops.ui.widgets.diff_view import DiffView

STATUS_COLORS = {"M": "warning", "A": "success", "?": "success", "D": "danger", "R": "accent", "C": "accent", "U": "danger"}
PATH_ROLE = Qt.ItemDataRole.UserRole
STATUS_ROLE = Qt.ItemDataRole.UserRole + 1


class ChangesView(QWidget):
    """Working-tree changes: stage files, hunks or single lines, then commit."""

    changed = Signal()  # index or HEAD moved; the window should refresh
    open_file_history = Signal(str)
    open_blame = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.backend: GitBackend | None = None
        self.patch: FilePatch | None = None
        self.current: tuple[str, bool] | None = None  # (path, staged side)
        self.current_letter = ""
        self.conflicts: list[str] = []
        self.operation: str | None = None  # merge, rebase, cherry-pick or revert in progress

        self.banner = QFrame()
        self.banner.setFrameShape(QFrame.Shape.StyledPanel)
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        self.continue_btn = QPushButton("Continue")
        self.continue_btn.clicked.connect(self.continue_operation)
        self.skip_btn = QPushButton("Skip this commit")
        self.skip_btn.clicked.connect(self.skip_commit)
        self.abort_btn = QPushButton("Abort…")
        self.abort_btn.clicked.connect(self.abort_operation)
        banner_row = QHBoxLayout(self.banner)
        banner_row.addWidget(self.banner_label, 1)
        banner_row.addWidget(self.continue_btn)
        banner_row.addWidget(self.skip_btn)
        banner_row.addWidget(self.abort_btn)
        self.banner.hide()

        self.unstaged = self._make_list()
        self.staged = self._make_list()
        self.unstaged_label = QLabel("Unstaged")
        self.staged_label = QLabel("Staged")
        stage_all = QPushButton("Stage all")
        stage_all.clicked.connect(lambda: self._run(lambda b: b.stage_all()))
        unstage_all = QPushButton("Unstage all")
        unstage_all.clicked.connect(lambda: self._run(lambda b: b.unstage_all()))

        self.message = QPlainTextEdit(placeholderText="Commit message\n\nSummary on the first line, details below.")
        self.message.setMaximumHeight(120)
        self.message.textChanged.connect(self._update_message_hint)
        self.message_hint = QLabel()
        self.message_hint.setStyleSheet("color: palette(placeholder-text);")
        self.amend = QCheckBox("Amend last commit")
        self.amend.toggled.connect(self._on_amend)
        self.commit_btn = QPushButton("Commit")
        self.commit_btn.setDefault(True)
        self.commit_btn.setToolTip("Ctrl+Enter")
        self.commit_btn.clicked.connect(self.commit)
        QShortcut(QKeySequence("Ctrl+Return"), self.message, activated=self.commit)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addLayout(self._header(self.unstaged_label, stage_all))
        left.addWidget(self.unstaged, 1)
        left.addLayout(self._header(self.staged_label, unstage_all))
        left.addWidget(self.staged, 1)
        left.addWidget(self.message)
        commit_row = QHBoxLayout()
        commit_row.addWidget(self.amend)
        commit_row.addWidget(self.message_hint)
        commit_row.addStretch(1)
        commit_row.addWidget(self.commit_btn)
        left.addLayout(commit_row)
        left_widget = QWidget()
        left_widget.setLayout(left)

        self.file_label = QLabel()
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hunk_btn = QPushButton("Stage hunk")
        self.hunk_btn.clicked.connect(self.apply_hunk)
        self.lines_btn = QPushButton("Stage selected lines")
        self.lines_btn.clicked.connect(self.apply_lines)
        self.discard_btn = QPushButton("Discard file…")
        self.discard_btn.clicked.connect(self.discard_current)
        self.resolve_btn = QPushButton("Resolve conflicts…")
        self.resolve_btn.clicked.connect(lambda: self.current and self.resolve(self.current[0]))
        diff_bar = QHBoxLayout()
        diff_bar.addWidget(self.file_label, 1)
        diff_bar.addWidget(self.hunk_btn)
        diff_bar.addWidget(self.lines_btn)
        diff_bar.addWidget(self.discard_btn)
        diff_bar.addWidget(self.resolve_btn)
        self.diff = DiffView()
        self.diff.setToolTip("Select lines, then stage or unstage just those lines")
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addLayout(diff_bar)
        right.addWidget(self.diff, 1)
        right_widget = QWidget()
        right_widget.setLayout(right)

        split = QSplitter(objectName="changes_split")
        split.addWidget(left_widget)
        split.addWidget(right_widget)
        split.setSizes([320, 760])
        layout = QVBoxLayout(self)
        layout.addWidget(self.banner)
        layout.addWidget(split)
        self._update_buttons()
        self.apply_theme()

    def apply_theme(self) -> None:
        c = colors()
        self.banner.setStyleSheet(
            f"QFrame {{ background: {c.banner_bg}; border: 1px solid {c.banner_border}; border-radius: 6px; }}"
            f" QLabel {{ color: {c.banner_fg}; border: none; }}"
        )
        self._update_message_hint()

    # ------------------------------------------------------------------ setup

    def _make_list(self) -> QListWidget:
        lst = QListWidget()
        lst.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        lst.itemSelectionChanged.connect(lambda l=lst: self._on_select(l))
        lst.itemDoubleClicked.connect(lambda _i, l=lst: self._toggle_selected(l))
        lst.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lst.customContextMenuRequested.connect(lambda pos, l=lst: self._context_menu(l, pos))
        return lst

    @staticmethod
    def _header(label: QLabel, button: QPushButton) -> QHBoxLayout:
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        row = QHBoxLayout()
        row.addWidget(label, 1)
        row.addWidget(button)
        return row

    def set_backend(self, backend: GitBackend | None) -> None:
        self.backend = backend
        self.current = None
        self.amend.setChecked(False)
        self.message.clear()
        self.refresh()

    # ------------------------------------------------------------------ status

    def refresh(self) -> int:
        """Reload status; returns the number of changed files."""
        if not self.backend:
            self.unstaged.clear()
            self.staged.clear()
            return 0
        try:
            statuses = self.backend.status()
        except GitError:
            return 0
        keep = self.current
        for lst in (self.unstaged, self.staged):
            lst.blockSignals(True)
            lst.clear()
        self.conflicts = [s.path for s in statuses if s.is_conflicted]
        for s in statuses:
            if s.is_conflicted:
                self._add_item(self.unstaged, s, "U")
                continue
            if s.is_unstaged:
                self._add_item(self.unstaged, s, s.worktree)
            if s.is_staged:
                self._add_item(self.staged, s, s.index)
        for lst in (self.unstaged, self.staged):
            lst.blockSignals(False)
        self.unstaged_label.setText(f"Unstaged ({self.unstaged.count()})")
        self.staged_label.setText(f"Staged ({self.staged.count()})")
        self._update_banner()

        if keep and not self._reselect(*keep) and not self._reselect(keep[0], not keep[1]):
            self.current = None
            self.patch = None
            self.diff.clear()
            self.file_label.clear()
        self._update_buttons()
        return len(statuses)

    @staticmethod
    def _add_item(lst: QListWidget, status: FileStatus, letter: str) -> None:
        label = f"{status.orig_path} → {status.path}" if status.orig_path and letter in "RC" else status.path
        it = QListWidgetItem(f"{letter}   {label}")
        it.setData(PATH_ROLE, status.path)
        it.setData(STATUS_ROLE, letter)
        it.setForeground(QBrush(QColor(getattr(colors(), STATUS_COLORS.get(letter, "muted")))))
        it.setToolTip(status.path)
        lst.addItem(it)

    def _reselect(self, path: str, staged: bool) -> bool:
        lst = self.staged if staged else self.unstaged
        for row in range(lst.count()):
            if lst.item(row).data(PATH_ROLE) == path:
                lst.setCurrentRow(row)
                self._show(path, staged, lst.item(row).data(STATUS_ROLE))
                return True
        return False

    # ------------------------------------------------------------------ diff

    def _on_select(self, lst: QListWidget) -> None:
        items = lst.selectedItems()
        if not items:
            return
        other = self.staged if lst is self.unstaged else self.unstaged
        other.blockSignals(True)
        other.clearSelection()
        other.blockSignals(False)
        it = items[-1]
        self._show(it.data(PATH_ROLE), lst is self.staged, it.data(STATUS_ROLE))

    def _show(self, path: str, staged: bool, letter: str) -> None:
        if not self.backend:
            return
        scroll = self.diff.verticalScrollBar().value() if self.current == (path, staged) else 0
        self.current = (path, staged)
        self.current_letter = letter
        try:
            if letter == "U":
                target = self.backend.root / path
                body = target.read_text(errors="replace") if target.is_file() else "(deleted on one side)"
                text = f"Conflicted file {path}. Double-click it or press Resolve conflicts…\n\n{body}"
                self.patch = None
            elif letter == "?":
                text = self.backend.untracked_preview(path)
                self.patch = None
            else:
                text = self.backend.file_diff(path, staged)
                self.patch = parse_file_patch(text)
        except (GitError, OSError) as exc:
            text, self.patch = f"Couldn't show this file: {exc}", None
        self.file_label.setText(f"{'Staged' if staged else 'Unstaged'} · {path}")
        self.diff.show_diff(text)
        self.diff.verticalScrollBar().setValue(scroll)
        self._update_buttons()

    def _update_buttons(self) -> None:
        staged = bool(self.current and self.current[1])
        verb = "Unstage" if staged else "Stage"
        self.hunk_btn.setText(f"{verb} hunk")
        self.lines_btn.setText(f"{verb} selected lines")
        can_lines = bool(self.patch and self.patch.can_stage_lines)
        self.hunk_btn.setEnabled(can_lines)
        self.lines_btn.setEnabled(can_lines)
        conflicted = bool(self.current) and self.current_letter == "U"
        self.discard_btn.setVisible(bool(self.current) and not staged and not conflicted)
        self.resolve_btn.setVisible(conflicted)

    def _selected_blocks(self) -> tuple[int, int]:
        cursor = self.diff.textCursor()
        doc = self.diff.document()
        start = doc.findBlock(cursor.selectionStart()).blockNumber()
        end = doc.findBlock(cursor.selectionEnd()).blockNumber()
        return start, end

    def apply_hunk(self) -> None:
        if not self.patch or not self.current:
            return
        hunk = self.patch.hunk_at(self._selected_blocks()[0])
        if hunk is None:
            hunk = self.patch.hunks[0] if len(self.patch.hunks) == 1 else None
        if hunk is None:
            self.window().statusBar().showMessage("Click inside a hunk first", 3000)
            return
        self._apply(hunk.change_indices)

    def apply_lines(self) -> None:
        if not self.patch or not self.current:
            return
        selected = self.patch.changes_in(*self._selected_blocks())
        if not selected:
            self.window().statusBar().showMessage("Select one or more + or − lines first", 3000)
            return
        self._apply(selected)

    def _apply(self, indices) -> None:
        assert self.patch and self.current
        reverse = self.current[1]
        patch_text = build_partial_patch(self.patch, indices, reverse=reverse)
        if patch_text:
            self._run(lambda b: b.apply_to_index(patch_text, reverse=reverse))

    # ------------------------------------------------------------------ file actions

    def _selected_paths(self, lst: QListWidget) -> list[str]:
        return [it.data(PATH_ROLE) for it in lst.selectedItems()]

    def _toggle_selected(self, lst: QListWidget) -> None:
        paths = self._selected_paths(lst)
        if not paths:
            return
        if lst is self.staged:
            self._run(lambda b: b.unstage(*paths))
        elif len(paths) == 1 and paths[0] in self.conflicts:
            self.resolve(paths[0])
        else:
            self._run(lambda b: b.stage(*paths))

    def _context_menu(self, lst: QListWidget, pos) -> None:
        paths = self._selected_paths(lst)
        if not paths:
            return
        menu = QMenu(self)
        if lst is self.staged:
            menu.addAction("Unstage", lambda: self._run(lambda b: b.unstage(*paths)))
        elif all(p in self.conflicts for p in paths):
            if len(paths) == 1:
                menu.addAction("Resolve conflicts…", lambda: self.resolve(paths[0]))
            menu.addAction("Mark resolved as is", lambda: self._run(lambda b: b.stage(*paths)))
        else:
            menu.addAction("Stage", lambda: self._run(lambda b: b.stage(*paths)))
            menu.addAction("Discard changes…", lambda: self._discard(paths))
            untracked = [p for p in paths if p not in self.conflicts and self._letter(p) == "?"]
            if untracked:
                ignore = menu.addMenu("Ignore")
                ignore.addAction(
                    "This file" if len(untracked) == 1 else f"These {len(untracked)} files",
                    lambda: self._ignore(["/" + p for p in untracked]),
                )
                suffixes = sorted({PurePosixPath(p).suffix for p in untracked if PurePosixPath(p).suffix})
                for suffix in suffixes:
                    ignore.addAction(f"All *{suffix} files", lambda s=suffix: self._ignore([f"*{s}"]))
                folders = sorted({str(PurePosixPath(p).parent) for p in untracked if "/" in p})
                for folder in folders:
                    ignore.addAction(f"Folder {folder}/", lambda f=folder: self._ignore([f"/{f}/"]))
        menu.addSeparator()
        if len(paths) == 1:
            menu.addAction("File history", lambda: self.open_file_history.emit(paths[0]))
            menu.addAction("Blame", lambda: self.open_blame.emit(paths[0], None))
        copy = QAction("Copy path", menu)
        copy.triggered.connect(lambda: QGuiApplication.clipboard().setText("\n".join(paths)))
        menu.addAction(copy)
        menu.exec(lst.viewport().mapToGlobal(pos))

    def _letter(self, path: str) -> str:
        for row in range(self.unstaged.count()):
            if self.unstaged.item(row).data(PATH_ROLE) == path:
                return self.unstaged.item(row).data(STATUS_ROLE)
        return ""

    def _ignore(self, rules: list[str]) -> None:
        def add(b: GitBackend) -> None:
            for rule in rules:
                b.add_ignore_rule(rule)

        self._run(add)

    def discard_current(self) -> None:
        if self.current and not self.current[1]:
            self._discard([self.current[0]])

    def _discard(self, paths: list[str]) -> None:
        if not self.backend:
            return
        letters = {it.data(PATH_ROLE): it.data(STATUS_ROLE) for it in (self.unstaged.item(r) for r in range(self.unstaged.count()))}
        untracked = [p for p in paths if letters.get(p) == "?"]
        tracked = [p for p in paths if p not in untracked]
        names = "\n".join(paths[:10]) + ("\n…" if len(paths) > 10 else "")
        what = "delete these untracked files" if untracked and not tracked else "discard your changes to"
        answer = QMessageBox.warning(
            self,
            "Discard changes",
            f"This will permanently {what}:\n\n{names}\n\nYou can't undo this.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Discard:
            return

        def do(b: GitBackend) -> None:
            if tracked:
                b.discard(*tracked)
            if untracked:
                b.delete_untracked(*untracked)

        self._run(do)

    # ------------------------------------------------------------------ commit

    SUBJECT_LIMIT = 72

    def _update_message_hint(self) -> None:
        lines = self.message.toPlainText().split("\n")
        subject = len(lines[0])
        if not subject:
            self.message_hint.clear()
            return
        problems = []
        if subject > self.SUBJECT_LIMIT:
            problems.append("subject is long")
        if len(lines) > 1 and lines[1].strip():
            problems.append("leave line 2 blank")
        color = colors().danger if problems else "palette(placeholder-text)"
        self.message_hint.setStyleSheet(f"color: {color};")
        self.message_hint.setText(f"{subject}/{self.SUBJECT_LIMIT}" + (f" · {', '.join(problems)}" if problems else ""))

    # ------------------------------------------------------------------ merge state

    def _update_banner(self) -> None:
        if not self.backend:
            self.banner.hide()
            return
        self.operation = self.backend.operation_in_progress()
        if not self.operation and not self.conflicts:
            self.banner.hide()
            return
        left = (
            f"{len(self.conflicts)} conflicted file(s) left. Resolve them, then "
            if self.conflicts
            else "All conflicts resolved. "
        )
        if self.operation == "merge":
            subject = self.backend.merge_message().split("\n", 1)[0]
            if not self.message.toPlainText().strip():
                self.message.setPlainText(self.backend.merge_message())
            text = f"<b>Merging</b> · {subject}<br>{left}commit to finish the merge."
        elif self.operation:
            name = {"rebase": "Rewriting history (rebase)", "cherry-pick": "Cherry-picking", "revert": "Reverting"}[self.operation]
            text = f"<b>{name}</b> stopped on a conflict.<br>{left}press Continue."
        else:
            text = f"<b>{len(self.conflicts)} conflicted file(s)</b> from applying a stash. Resolve them to continue."
        self.banner_label.setText(text)
        self.continue_btn.setVisible(self.operation in ("rebase", "cherry-pick", "revert"))
        self.continue_btn.setEnabled(not self.conflicts)
        self.skip_btn.setVisible(self.operation in ("rebase", "cherry-pick"))
        self.abort_btn.setVisible(self.operation is not None)
        self.banner.show()

    def resolve(self, path: str) -> None:
        if not self.backend:
            return
        if ConflictEditor(self.backend, path, self).exec():
            self.refresh()
            self.changed.emit()

    def abort_operation(self) -> None:
        what = {"merge": "merge", "rebase": "history rewrite", "cherry-pick": "cherry-pick", "revert": "revert"}.get(
            self.operation or "", "operation"
        )
        answer = QMessageBox.warning(
            self,
            f"Abort {what}",
            f"Stop the {what} and put everything back the way it was before it started? "
            "Any conflict resolutions you made will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes and self._run(lambda b: b.abort_operation()):
            self.message.clear()

    def continue_operation(self) -> None:
        self._run_stoppable(lambda b: b.continue_operation())

    def skip_commit(self) -> None:
        self._run_stoppable(lambda b: b.skip_commit())

    def _run_stoppable(self, fn) -> None:
        conflicts: list[str] = []
        if self._run(lambda b: conflicts.extend(fn(b))) and conflicts:
            QMessageBox.information(
                self, "More conflicts", f"The next commit conflicts too: {len(conflicts)} file(s) to resolve."
            )
        elif self.backend and not self.backend.operation_in_progress():
            self.window().statusBar().showMessage("Done", 4000)

    def _on_amend(self, on: bool) -> None:
        if on and self.backend and not self.message.toPlainText().strip():
            self.message.setPlainText(self.backend.last_commit_message())
        self.commit_btn.setText("Amend commit" if on else "Commit")

    def commit(self) -> None:
        if not self.backend:
            return
        message = self.message.toPlainText().strip()
        amend = self.amend.isChecked()
        if self.conflicts:
            QMessageBox.information(
                self, "Unresolved conflicts", f"Resolve the {len(self.conflicts)} conflicted file(s) before committing."
            )
            return
        if self.operation in ("rebase", "cherry-pick", "revert"):
            self.continue_operation()  # git makes the commit itself when continuing
            return
        if not message:
            self.message.setFocus()
            self.window().statusBar().showMessage("Write a commit message first", 3000)
            return
        if self.staged.count() == 0 and not amend:
            if self.unstaged.count() == 0:
                self.window().statusBar().showMessage("Nothing to commit", 3000)
                return
            answer = QMessageBox.question(self, "Nothing staged", "Nothing is staged. Stage all changes and commit them?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            if not self._run(lambda b: b.stage_all(), emit=False):
                return
        if self._run(lambda b: b.commit(message, amend=amend)):
            self.message.clear()
            self.amend.setChecked(False)
            self.window().statusBar().showMessage("Amended last commit" if amend else "Committed", 4000)

    # ------------------------------------------------------------------ helpers

    def _run(self, fn, emit: bool = True) -> bool:
        """Run a quick local git operation, report errors, refresh. Returns success."""
        if not self.backend:
            return False
        try:
            fn(self.backend)
        except GitError as exc:
            QMessageBox.warning(self, "Git", exc.stderr or str(exc))
            self.refresh()
            return False
        self.refresh()
        if emit:
            self.changed.emit()
        return True
