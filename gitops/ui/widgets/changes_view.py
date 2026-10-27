"""The Changes workbench: pick files with checkboxes, stage hunks or lines in place, then commit."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gitops.core.encoding import display_text
from gitops.core.git_backend import GitBackend, GitError
from gitops.core.models import FileStatus
from gitops.core.patch import FilePatch, build_partial_patch, parse_file_patch
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui.dialogs.conflict_editor import ConflictEditor
from gitops.ui.theme import colors
from gitops.ui.widgets.commit_composer import CommitComposer
from gitops.ui.widgets.diff_view import DiffView
from gitops.ui.widgets.file_list import ChangeFileList, FileRow
from gitops.ui.widgets.hunk_view import HunkDiffView

Counts = dict[str, tuple[int | None, int | None]]


@dataclass(frozen=True)
class WorkingState:
    statuses: list[FileStatus]
    operation: str | None
    merge_message: str
    branch: str | None = None
    unstaged_counts: Counts = field(default_factory=dict)
    staged_counts: Counts = field(default_factory=dict)


def load_state(backend: GitBackend) -> WorkingState:
    """Everything the Changes view shows, gathered in one go (safe to run on a worker thread)."""
    operation = backend.operation_in_progress()
    unstaged_counts, staged_counts = backend.change_counts()
    return WorkingState(
        backend.status(),
        operation,
        backend.merge_message() if operation == "merge" else "",
        backend.current_branch(),
        unstaged_counts,
        staged_counts,
    )


def build_rows(state: WorkingState) -> tuple[list[FileRow], list[FileRow]]:
    staged: list[FileRow] = []
    unstaged: list[FileRow] = []
    staged_paths = {s.path for s in state.statuses if s.is_staged}
    for s in state.statuses:
        if s.is_conflicted:
            unstaged.append(FileRow(s.path, False, "U", note="conflict"))
            continue
        if s.is_staged:
            added, removed = state.staged_counts.get(s.path, (None, None))
            staged.append(FileRow(s.path, True, s.index, added, removed, orig_path=s.orig_path))
        if s.is_unstaged:
            added, removed = state.unstaged_counts.get(s.path, (None, None))
            note = "partly staged" if s.path in staged_paths else ""
            unstaged.append(FileRow(s.path, False, s.worktree, added, removed, note=note))
    return staged, unstaged


def _chip(label: QLabel, bg: str, fg: str) -> None:
    label.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 10px; padding: 1px 8px;")


class ChangesView(QWidget):
    """Working-tree changes: stage files, hunks or single lines, then commit."""

    changed = Signal()  # index or HEAD moved; the window should refresh
    open_file_history = Signal(str)
    open_blame = Signal(str, object)
    count_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent, objectName="ChangesPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.backend: GitBackend | None = None
        self.patch: FilePatch | None = None
        self.current: FileRow | None = None
        self.conflicts: list[str] = []
        self.operation: str | None = None  # merge, rebase, cherry-pick or revert in progress
        self.merge_message = ""
        self.branch: str | None = None
        self._generation = 0  # bumped by every refresh; stale background results are dropped

        # ---- banner for merges/rebases/cherry-picks that stopped on conflicts
        self.banner = QFrame(objectName="Banner")
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        self.continue_btn = QPushButton("Continue")
        self.continue_btn.setProperty("variant", "primary")
        self.continue_btn.clicked.connect(self.continue_operation)
        self.skip_btn = QPushButton("Skip this commit")
        self.skip_btn.setProperty("variant", "secondary")
        self.skip_btn.clicked.connect(self.skip_commit)
        self.abort_btn = QPushButton("Abort…")
        self.abort_btn.setProperty("variant", "danger")
        self.abort_btn.clicked.connect(self.abort_operation)
        banner_row = QHBoxLayout(self.banner)
        banner_row.setContentsMargins(14, 10, 10, 10)
        banner_row.addWidget(self.banner_label, 1)
        for b in (self.continue_btn, self.skip_btn, self.abort_btn):
            banner_row.addWidget(b)
        self.banner.hide()

        # ---- left panel: files + composer
        title = QLabel("Changes", objectName="Title")
        self.stage_all_btn = QPushButton("Stage all")
        self.stage_all_btn.setProperty("variant", "secondary")
        self.stage_all_btn.clicked.connect(lambda: self._run(lambda b: b.stage_all()))
        self.unstage_all_btn = QPushButton("Unstage all")
        self.unstage_all_btn.setProperty("variant", "secondary")
        self.unstage_all_btn.clicked.connect(lambda: self._run(lambda b: b.unstage_all()))
        head = QHBoxLayout()
        head.setContentsMargins(16, 14, 12, 6)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.unstage_all_btn)
        head.addWidget(self.stage_all_btn)

        self.files = ChangeFileList()
        self.files.file_selected.connect(self._show)
        self.files.toggle_requested.connect(self._toggle)
        self.files.activated_row.connect(self._activate)
        self.files.menu_requested.connect(self._context_menu)
        self.empty_files = QLabel("No changes.\nYour working tree matches the last commit.", objectName="EmptyState")
        self.empty_files.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.composer = CommitComposer()
        self.composer.commit_requested.connect(self.commit)
        self.composer.amend_toggled.connect(self._on_amend)

        left = QFrame(objectName="Panel")
        left.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left.setMinimumWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addLayout(head)
        left_layout.addWidget(self.files, 1)
        left_layout.addWidget(self.empty_files, 1)
        left_layout.addWidget(self.composer)

        # ---- right: file header + diff
        self.path_label = QLabel()
        self.path_label.setTextFormat(Qt.TextFormat.RichText)
        self.add_chip = QLabel()
        self.del_chip = QLabel()
        self.note_label = QLabel(objectName="Muted")
        self.discard_btn = QPushButton("Discard file…")
        self.discard_btn.setProperty("variant", "danger")
        self.discard_btn.clicked.connect(self.discard_current)
        self.resolve_btn = QPushButton("Resolve conflicts…")
        self.resolve_btn.setProperty("variant", "primary")
        self.resolve_btn.clicked.connect(lambda: self.current and self.resolve(self.current.path))
        self.file_btn = QPushButton("Stage file")
        self.file_btn.setProperty("variant", "secondary")
        self.file_btn.clicked.connect(lambda: self.current and self._toggle(self.current))
        self.more_btn = QToolButton(text="···")
        self.more_btn.setProperty("variant", "ghost")
        self.more_btn.setAccessibleName("More file actions")
        self.more_btn.clicked.connect(lambda: self.current and self._context_menu([self.current], self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft())))
        header = QFrame(objectName="DiffHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(20, 10, 16, 10)
        header_row.setSpacing(10)
        header_row.addWidget(self.path_label)
        header_row.addWidget(self.add_chip)
        header_row.addWidget(self.del_chip)
        header_row.addWidget(self.note_label)
        header_row.addStretch(1)
        for w in (self.more_btn, self.discard_btn, self.resolve_btn, self.file_btn):
            header_row.addWidget(w)
        self.header = header

        self.hunks = HunkDiffView()
        self.hunks.hunk_action.connect(self._apply_hunk)
        self.hunks.selection_changed.connect(self._update_selection_bar)
        self.text_view = DiffView()  # untracked, conflicted, binary or too-large files
        self.empty_diff = QLabel("Select a file to see its changes", objectName="EmptyState")
        self.empty_diff.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack = QStackedWidget()
        for w in (self.empty_diff, self.hunks, self.text_view):
            self.stack.addWidget(w)

        self.diff_area = QWidget()
        area_layout = QVBoxLayout(self.diff_area)
        area_layout.setContentsMargins(0, 0, 0, 0)
        area_layout.setSpacing(0)
        area_layout.addWidget(header)
        area_layout.addWidget(self.stack, 1)

        # Floating bar for line selections, centred over the bottom of the diff.
        self.selection_bar = QFrame(self.diff_area, objectName="SelectionBar")
        self.selection_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.selection_label = QLabel()
        self.stage_lines_btn = QPushButton("Stage lines")
        self.stage_lines_btn.setProperty("variant", "primary")
        self.stage_lines_btn.clicked.connect(self.apply_selected_lines)
        self.discard_lines_btn = QPushButton("Discard lines…")
        self.discard_lines_btn.clicked.connect(self.discard_selected_lines)
        clear = QPushButton("✕")
        clear.setAccessibleName("Clear selection")
        clear.setFixedWidth(34)
        clear.clicked.connect(self.hunks.clear_selection)
        bar = QHBoxLayout(self.selection_bar)
        bar.setContentsMargins(16, 8, 8, 8)
        bar.setSpacing(8)
        bar.addWidget(self.selection_label)
        bar.addWidget(self.stage_lines_btn)
        bar.addWidget(self.discard_lines_btn)
        bar.addWidget(clear)
        self.selection_bar.hide()
        self.diff_area.installEventFilter(self)

        split = QSplitter(objectName="changes_split")
        split.addWidget(left)
        split.addWidget(self.diff_area)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 900])
        split.setHandleWidth(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        banner_wrap = QVBoxLayout()
        banner_wrap.setContentsMargins(12, 10, 12, 0)
        banner_wrap.addWidget(self.banner)
        layout.addLayout(banner_wrap)
        layout.addWidget(split, 1)
        self._show_nothing()
        self.apply_theme()

    def apply_theme(self) -> None:
        c = colors()
        _chip(self.add_chip, c.success_soft, c.success_on_soft)
        _chip(self.del_chip, c.danger_soft, c.danger_on_soft)
        self.composer.apply_theme()
        if self.current:
            self._set_header(self.current)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.diff_area and event.type() == QEvent.Type.Resize:
            self._place_selection_bar()
        return super().eventFilter(obj, event)

    def set_backend(self, backend: GitBackend | None) -> None:
        self.backend = backend
        self.current = None
        self.composer.amend.setChecked(False)
        self.composer.clear()
        self.refresh()

    # ------------------------------------------------------------------ status

    def refresh(self) -> int:
        """Reload status now (right after the user's own action); returns the number of changed files."""
        self._generation += 1
        if not self.backend:
            self.files.set_rows([], [])
            return 0
        try:
            state = load_state(self.backend)
        except GitError:
            return 0
        return self._apply_state(state)

    def refresh_async(self, runner: TaskRunner, on_done: Callable[[int], None] | None = None) -> None:
        """Reload status in the background (e.g. when the window regains focus).

        A result is dropped if a newer refresh, sync or async, started after it,
        so a slow background status can't overwrite what the user just did.
        """
        if not self.backend:
            return
        self._generation += 1
        generation, backend = self._generation, self.backend

        def apply(state: WorkingState) -> None:
            if generation == self._generation and backend is self.backend:
                count = self._apply_state(state)
                if on_done:
                    on_done(count)

        task = Task(load_state, backend)
        task.signals.result.connect(apply)
        runner.submit(task)

    def _apply_state(self, state: WorkingState) -> int:
        self.operation = state.operation
        self.merge_message = state.merge_message
        self.branch = state.branch
        self.conflicts = [s.path for s in state.statuses if s.is_conflicted]
        staged, unstaged = build_rows(state)
        keep = self.current
        self.files.set_rows(staged, unstaged)
        has_files = bool(staged or unstaged)
        self.files.setVisible(has_files)
        self.empty_files.setVisible(not has_files)
        self.stage_all_btn.setVisible(bool(unstaged))
        self.unstage_all_btn.setVisible(bool(staged))
        self._update_banner()
        self._update_composer(len(staged), len(unstaged))

        reselected = False
        if keep:
            for staged_side in (keep.staged, not keep.staged):
                row = next((r for r in self.files.rows() if r.path == keep.path and r.staged == staged_side), None)
                if row:
                    self.files.select(row.path, row.staged)
                    reselected = True
                    break
        if not reselected:
            self.current = None
            self._show_nothing()
        count = len(state.statuses)
        self.count_changed.emit(count)
        return count

    # ------------------------------------------------------------------ queries (also used by tests)

    def staged_paths(self) -> list[str]:
        return self.files.paths(staged=True)

    def unstaged_paths(self) -> list[str]:
        return self.files.paths(staged=False)

    def select_file(self, path: str, staged: bool = False) -> bool:
        return self.files.select(path, staged)

    def current_text(self) -> str:
        if self.stack.currentWidget() is self.hunks:
            return self.hunks.plain_text()
        if self.stack.currentWidget() is self.text_view:
            return self.text_view.toPlainText()
        return ""

    def _row(self, path: str, staged: bool) -> FileRow | None:
        return next((r for r in self.files.rows() if r.path == path and r.staged == staged), None)

    # ------------------------------------------------------------------ showing a file

    def _show_nothing(self) -> None:
        self.patch = None
        self.header.hide()
        self.stack.setCurrentWidget(self.empty_diff)
        self.selection_bar.hide()

    def _set_header(self, row: FileRow) -> None:
        c = colors()
        parts = [p for p in row.folder.strip("/").split("/") if p]
        crumbs = f" <span style='color:{c.text_secondary}'>/</span> ".join(
            f"<span style='color:{c.text_secondary}'>{p}</span>" for p in parts
        )
        name = f"<span style='color:{c.text}; font-weight:600'>{row.name}</span>"
        self.path_label.setText(f"{crumbs} <span style='color:{c.text_secondary}'>/</span> {name}" if crumbs else name)
        self.path_label.setToolTip(row.path)

    def _show(self, row: FileRow) -> None:
        if not self.backend or row is None:
            return
        same_file = self.current is not None and (self.current.path, self.current.staged) == (row.path, row.staged)
        scroll = self.hunks.verticalScrollBar().value() if same_file else 0
        self.current = row
        self.header.show()
        self._set_header(row)

        conflicted = row.letter == "U"
        untracked = row.letter == "?"
        self.resolve_btn.setVisible(conflicted)
        self.file_btn.setVisible(not conflicted)
        self.file_btn.setText("Unstage file" if row.staged else "Stage file")
        self.discard_btn.setVisible(not row.staged and not conflicted)
        self.discard_btn.setText("Delete file…" if untracked else "Discard file…")
        has_counts = row.added is not None or row.removed is not None
        self.add_chip.setVisible(has_counts and not conflicted)
        self.del_chip.setVisible(has_counts and not conflicted)
        self.add_chip.setText(f"+{row.added or 0}")
        self.del_chip.setText(f"−{row.removed or 0}")
        notes = {"U": "both branches changed this file", "?": "new file, not tracked yet"}
        self.note_label.setText(row.note if row.note and row.note != "conflict" else notes.get(row.letter, ""))

        try:
            if conflicted:
                target = self.backend.root / row.path
                if target.is_file():
                    with target.open("rb") as handle:
                        body = handle.read(self.backend.PREVIEW_BYTES).decode("utf-8", errors="replace")
                else:
                    body = "(deleted on one side)"
                self._show_text(f"Both branches changed this file. Press Resolve conflicts… to pick what to keep.\n\n{body}")
                return
            if untracked:
                self._show_text(self.backend.untracked_preview(row.path))
                return
            text = self.backend.file_diff(row.path, row.staged)
        except (GitError, OSError) as exc:
            self._show_text(f"Couldn't show this file: {exc}")
            return

        patch = parse_file_patch(text) if len(text) <= DiffView.MAX_CHARS else None
        if patch is None or not patch.can_stage_lines:
            # Binary, mode-only or huge diffs: show as text and stage the whole file.
            self.patch = None
            self._show_text(display_text(text) if text.strip() else "No line changes (only file mode or metadata).")
            return
        self.patch = patch
        self.hunks.show_patch(patch, "Unstage hunk" if row.staged else "Stage hunk")
        self.stack.setCurrentWidget(self.hunks)
        if same_file:
            self.hunks.verticalScrollBar().setValue(scroll)
        self._update_selection_bar(0)

    def _show_text(self, text: str) -> None:
        self.patch = None
        self.text_view.show_diff(text)
        self.stack.setCurrentWidget(self.text_view)
        self.selection_bar.hide()

    # ------------------------------------------------------------------ staging

    def _toggle(self, row: FileRow) -> None:
        if row.staged:
            self._run(lambda b: b.unstage(row.path))
        elif row.letter == "U":
            self.resolve(row.path)
        else:
            self._run(lambda b: b.stage(row.path))

    def _activate(self, row: FileRow) -> None:
        if row.letter == "U":
            self.resolve(row.path)
        else:
            self._toggle(row)

    def _apply_hunk(self, hunk_no: int) -> None:
        if self.patch and 0 <= hunk_no < len(self.patch.hunks):
            self._apply_to_index(self.patch.hunks[hunk_no].change_indices)

    def apply_selected_lines(self) -> None:
        if self.hunks.selected:
            self._apply_to_index(set(self.hunks.selected))

    def _apply_to_index(self, indices) -> None:
        if not self.patch or not self.current:
            return
        reverse = self.current.staged
        patch_text = build_partial_patch(self.patch, indices, reverse=reverse)
        if patch_text:
            self._run(lambda b: b.apply_to_index(patch_text, reverse=reverse))

    def discard_selected_lines(self) -> None:
        if not self.patch or not self.current or self.current.staged or not self.hunks.selected:
            return
        n = len(self.hunks.selected)
        answer = QMessageBox.warning(
            self,
            "Discard lines",
            f"Throw away {n} selected line change{'s' if n != 1 else ''} in {self.current.name}? You can't undo this.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Discard:
            return
        # Built against the working tree (reverse=True) and applied in reverse there.
        patch_text = build_partial_patch(self.patch, set(self.hunks.selected), reverse=True)
        if patch_text:
            self._run(lambda b: b.discard_from_worktree(patch_text))

    def _update_selection_bar(self, count: int) -> None:
        staged = bool(self.current and self.current.staged)
        if count == 0 or self.stack.currentWidget() is not self.hunks:
            self.selection_bar.hide()
            return
        self.selection_label.setText(f"{count} line{'s' if count != 1 else ''} selected")
        self.stage_lines_btn.setText("Unstage lines" if staged else "Stage lines")
        self.discard_lines_btn.setVisible(not staged)
        self.selection_bar.adjustSize()
        self._place_selection_bar()
        self.selection_bar.show()
        self.selection_bar.raise_()

    def _place_selection_bar(self) -> None:
        bar = self.selection_bar
        bar.adjustSize()
        area = self.diff_area.rect()
        bar.move(area.center().x() - bar.width() // 2, area.bottom() - bar.height() - 20)

    # ------------------------------------------------------------------ file actions

    def _context_menu(self, rows: list[FileRow], global_pos) -> None:
        if not rows:
            return
        paths = [r.path for r in rows]
        staged = rows[0].staged
        menu = QMenu(self)
        if staged:
            menu.addAction("Unstage", lambda: self._run(lambda b: b.unstage(*paths)))
        elif all(p in self.conflicts for p in paths):
            if len(paths) == 1:
                menu.addAction("Resolve conflicts…", lambda: self.resolve(paths[0]))
            menu.addAction("Mark resolved as is", lambda: self._run(lambda b: b.stage(*paths)))
        else:
            menu.addAction("Stage", lambda: self._run(lambda b: b.stage(*paths)))
            menu.addAction("Discard changes…", lambda: self._discard(paths))
            untracked = [r.path for r in rows if r.letter == "?"]
            if untracked:
                ignore = menu.addMenu("Ignore")
                ignore.addAction(
                    "This file" if len(untracked) == 1 else f"These {len(untracked)} files",
                    lambda: self._ignore(["/" + p for p in untracked]),
                )
                for suffix in sorted({PurePosixPath(p).suffix for p in untracked if PurePosixPath(p).suffix}):
                    ignore.addAction(f"All *{suffix} files", lambda s=suffix: self._ignore([f"*{s}"]))
                for folder in sorted({str(PurePosixPath(p).parent) for p in untracked if "/" in p}):
                    ignore.addAction(f"Folder {folder}/", lambda f=folder: self._ignore([f"/{f}/"]))
        menu.addSeparator()
        if len(paths) == 1:
            menu.addAction("File history", lambda: self.open_file_history.emit(paths[0]))
            menu.addAction("Blame", lambda: self.open_blame.emit(paths[0], None))
        menu.addAction("Copy path", lambda: QGuiApplication.clipboard().setText("\n".join(paths)))
        menu.exec(global_pos)

    def _ignore(self, rules: list[str]) -> None:
        def add(b: GitBackend) -> None:
            for rule in rules:
                b.add_ignore_rule(rule)

        self._run(add)

    def discard_current(self) -> None:
        if self.current and not self.current.staged:
            self._discard([self.current.path])

    def _discard(self, paths: list[str]) -> None:
        if not self.backend:
            return
        letters = {r.path: r.letter for r in self.files.rows() if not r.staged}
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

    # ------------------------------------------------------------------ merge state

    def _update_banner(self) -> None:
        if not self.backend or (not self.operation and not self.conflicts):
            self.banner.hide()
            return
        left = (
            f"{len(self.conflicts)} conflicted file(s) left. Resolve them, then "
            if self.conflicts
            else "All conflicts resolved. "
        )
        if self.operation == "merge":
            subject = self.merge_message.split("\n", 1)[0]
            if self.composer.is_empty():
                self.composer.set_message(self.merge_message)
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
            self.composer.clear()

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
            self._status("Done")

    # ------------------------------------------------------------------ commit

    def _update_composer(self, staged: int, unstaged: int) -> None:
        branch = self.branch or "detached HEAD"
        if self.operation in ("rebase", "cherry-pick", "revert"):
            label = f"Continue {self.operation}"
        elif self.operation == "merge":
            label = "Commit merge"
        elif self.composer.amend.isChecked():
            label = "Amend last commit" + (f" with {staged} file{'s' if staged != 1 else ''}" if staged else "")
        elif staged:
            label = f"Commit {staged} file{'s' if staged != 1 else ''} to {branch}"
        elif unstaged:
            label = f"Commit all {unstaged} change{'s' if unstaged != 1 else ''} to {branch}"
        else:
            label = "Nothing to commit"
        self.composer.set_action(label)

    def _on_amend(self, on: bool) -> None:
        if on and self.backend and self.composer.is_empty():
            self.composer.set_message(self.backend.last_commit_message())
        self._update_composer(len(self.staged_paths()), len(self.unstaged_paths()))

    def commit(self) -> None:
        if not self.backend:
            return
        message = self.composer.message()
        amend = self.composer.amend.isChecked()
        if self.conflicts:
            QMessageBox.information(
                self, "Unresolved conflicts", f"Resolve the {len(self.conflicts)} conflicted file(s) before committing."
            )
            return
        if self.operation in ("rebase", "cherry-pick", "revert"):
            self.continue_operation()  # git makes the commit itself when continuing
            return
        if not message:
            self.composer.summary.setFocus()
            self._status("Write a summary first")
            return
        if not self.staged_paths() and not amend:
            if not self.unstaged_paths():
                self._status("Nothing to commit")
                return
            answer = QMessageBox.question(self, "Nothing staged", "Nothing is staged. Stage all changes and commit them?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            if not self._run(lambda b: b.stage_all(), emit=False):
                return
        if self._run(lambda b: b.commit(message, amend=amend)):
            self.composer.clear()
            self.composer.amend.setChecked(False)
            self._status("Amended last commit" if amend else "Committed")

    # ------------------------------------------------------------------ helpers

    def _status(self, text: str) -> None:
        window = self.window()
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(text, 4000)

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
