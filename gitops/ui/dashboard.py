"""Repositories dashboard: every repo you track as a card, with its state and the one next step."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import shiboken6

from gitops.core.git_backend import GitBackend
from gitops.core.models import RepoSummary
from gitops.services.task_runner import Task, TaskRunner
from gitops.ui import icons
from gitops.ui.theme import colors
from gitops.ui.widgets.common import relative_time

CARD_MIN_WIDTH = 320
FILTERS = ("all", "attention", "behind", "clean")
FILTER_LABELS = {"all": "All", "attention": "Needs attention", "behind": "Behind", "clean": "Clean"}


@dataclass(frozen=True)
class CardState:
    kind: str  # error, conflict, busy, changes, ahead, behind, clean
    chip: str  # status chip text
    action: str | None  # what the second button does: resolve, continue, commit, pull, push, remove
    action_label: str = ""

    @property
    def groups(self) -> set[str]:
        """Which filters show this card."""
        groups = {"all"}
        if self.kind in ("error", "conflict", "busy", "changes", "ahead"):
            groups.add("attention")
        if self.kind == "behind" or (self.action == "pull"):
            groups.add("behind")
        if self.kind == "clean":
            groups.add("clean")
        return groups


OPERATION_CHIPS = {"merge": "Merge in progress", "rebase": "Rebase paused", "cherry-pick": "Cherry-pick paused", "revert": "Revert paused"}


def card_state(s: RepoSummary) -> CardState:
    """The chip says what's going on; the action is the single most useful next step."""
    if s.error:
        return CardState("error", "Unavailable", "remove", "Remove")
    ahead, behind = s.ahead_behind or (0, 0)
    if s.conflicts:
        return CardState("conflict", f"{s.conflicts} conflict{'s' if s.conflicts != 1 else ''}", "resolve", "Resolve")
    if s.operation:
        return CardState("busy", OPERATION_CHIPS.get(s.operation, f"{s.operation} in progress"), "continue", "Continue")
    # Sync comes first: pulling before committing or pushing avoids surprises.
    if behind:
        chip = f"{s.changed} change{'s' if s.changed != 1 else ''}" if s.changed else f"Behind {behind}"
        return CardState("changes" if s.changed else "behind", chip, "pull", f"Pull {behind}")
    if s.changed:
        if ahead:
            return CardState("changes", f"{s.changed} change{'s' if s.changed != 1 else ''}", "push", f"Push {ahead}")
        return CardState("changes", f"{s.changed} change{'s' if s.changed != 1 else ''}", "commit", "Commit…")
    if ahead:
        return CardState("ahead", f"Ahead {ahead}", "push", f"Push {ahead}")
    if s.ahead_behind is None:
        return CardState("clean", "No upstream" if s.branch else "Detached", None)
    return CardState("clean", "Up to date", None)


def _chip_colors(kind: str) -> tuple[str, str, str]:
    """(background, text, card border) for a state, from the active theme."""
    c = colors()
    return {
        "error": (c.danger_soft, c.danger_on_soft, c.danger_border),
        "conflict": (c.danger_soft, c.danger_on_soft, c.danger_border),
        "busy": (c.accent_soft, c.accent_on_soft, c.accent_soft_border),
        "changes": (c.warning_soft, c.warning_on_soft, c.border),
        "ahead": (c.accent_soft, c.accent_on_soft, c.border),
        "behind": (c.accent_soft, c.accent_on_soft, c.border),
        "clean": (c.success_soft, c.success_on_soft, c.border),
    }[kind]


def _summaries(paths: list[str]):
    for path in paths:
        yield GitBackend.summarize_repo(path)


def _fetch_all(paths: list[str]):
    """Fetch every repo in turn, yielding (path, progress line); one failure doesn't stop the rest."""
    for path in paths:
        try:
            backend = GitBackend(path)
            if not backend.remotes():
                yield path, "no remote"
                continue
            for line in backend.fetch():
                yield path, line
            yield path, "done"
        except Exception as exc:  # reported on that repo's card
            yield path, f"failed: {str(exc).split('): ', 1)[-1][:160]}"


def _sync_repo(path: str, action: str):
    backend = GitBackend(path)
    yield from backend.pull() if action == "pull" else backend.push()


# ---------------------------------------------------------------------- card


class RepoCard(QFrame):
    open_requested = Signal(str, str)  # (path, page): page is "changes" or "history"
    action_requested = Signal(str, str)  # (path, action)
    remove_requested = Signal(str)

    def __init__(self, summary: RepoSummary, parent=None):
        super().__init__(parent, objectName="RepoCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Ignored horizontally: every column gets the same width, whatever the card's text.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Maximum)
        self.setMinimumWidth(CARD_MIN_WIDTH - 40)
        self.summary = summary
        self.state = card_state(summary)
        self.busy_text = ""  # an action is running: its progress
        self.note = ""  # e.g. why the last action failed; doesn't block retrying

        self.name = QLabel(summary.name)
        self.name.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.path = QLabel(objectName="Muted")
        self.path.setToolTip(summary.path)
        self.chip = QLabel()
        top = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(self.name)
        names.addWidget(self.path)
        top.addLayout(names, 1)
        top.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignTop)

        self.branch = QLabel()
        self.branch.setTextFormat(Qt.TextFormat.RichText)
        self.meta = QLabel(objectName="Muted")
        self.status = QLabel(objectName="Muted")  # live progress of an action on this repo (one line, elided)
        self.status.hide()
        divider = QFrame(objectName="PanelDivider")
        divider.setFixedHeight(1)
        self.last = QLabel()
        self.last_meta = QLabel(objectName="Muted")

        self.open_btn = QPushButton("Open")
        self.open_btn.setProperty("variant", "secondary")
        self.open_btn.clicked.connect(lambda: self.open_requested.emit(summary.path, "changes"))
        self.action_btn = QPushButton()
        self.action_btn.setProperty("variant", "primary")
        self.action_btn.clicked.connect(lambda: self.action_requested.emit(summary.path, self.state.action or ""))
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.open_btn, 1)
        buttons.addWidget(self.action_btn, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self.branch)
        layout.addWidget(self.status)
        layout.addWidget(divider)
        layout.addWidget(self.last)
        layout.addWidget(self.last_meta)
        layout.addLayout(buttons)
        self.render()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("Open", lambda: self.open_requested.emit(self.summary.path, "changes"))
        menu.addAction("Open history", lambda: self.open_requested.emit(self.summary.path, "history"))
        menu.addSeparator()
        menu.addAction("Remove from dashboard", lambda: self.remove_requested.emit(self.summary.path))
        menu.exec(event.globalPos())

    def mouseDoubleClickEvent(self, _event) -> None:
        self.open_requested.emit(self.summary.path, "changes")

    def set_busy(self, text: str) -> None:
        self.busy_text = text
        self.note = ""
        self.render()

    def set_note(self, text: str) -> None:
        self.busy_text = ""
        self.note = text
        self.render()

    def update_summary(self, summary: RepoSummary) -> None:
        self.summary = summary
        self.state = card_state(summary)
        self.busy_text = ""
        self.render()

    def render(self) -> None:
        """Full update for a new state or theme. Never called from resizeEvent: restyling there recurses."""
        c = colors()
        s, st = self.summary, self.state
        bg, fg, border = _chip_colors(st.kind)
        self.chip.setText(st.chip)
        self.chip.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 10px; padding: 3px 10px; font-weight: 600;")
        self.setStyleSheet(
            f"QFrame#RepoCard {{ background: {c.surface}; border: 1px solid {border}; border-radius: 12px; }}"
            f" QFrame#RepoCard QLabel {{ background: transparent; border: none; }}"
        )
        self._elide()
        self.open_btn.setVisible(not s.error)
        if s.error:
            self.branch.setText(f"<span style='color:{c.danger_on_soft}'>Can't open this folder</span>")
            self.branch.setToolTip(s.error)
            self.last.setText("Moved, deleted, or not a git repository any more.")
            self.last_meta.clear()
        else:
            ahead_behind = f"↑{s.ahead_behind[0]} ↓{s.ahead_behind[1]}" if s.ahead_behind else "no upstream"
            changes = "clean" if not s.changed else f"{s.changed} file{'s' if s.changed != 1 else ''} changed"
            stashes = f" · {s.stashes} stash{'es' if s.stashes != 1 else ''}" if s.stashes else ""
            self.branch.setText(
                f"<span style='color:{c.text}'>⎇ {s.branch or 'detached HEAD'}</span>"
                f"&nbsp;&nbsp;<span style='color:{c.text_secondary}; font-family:monospace'>{ahead_behind}</span>"
                f"&nbsp;&nbsp;<span style='color:{c.text_secondary}'>{changes}{stashes}</span>"
            )
            if s.last_commit:
                self.last.setToolTip(s.last_commit.subject)
                self.last_meta.setText(f"{s.last_commit.author} · {relative_time(s.last_commit.date)}")
            else:
                self.last.setText("No commits yet")
                self.last_meta.clear()
        shown = self.busy_text or self.note
        self.status.setVisible(bool(shown))
        self.status.setToolTip(shown)
        self.status.setStyleSheet(f"color: {c.danger_on_soft};" if self.note and not self.busy_text else "")
        self.status.setText(self.status.fontMetrics().elidedText(shown, Qt.TextElideMode.ElideRight, max(self.width() - 40, 160)))
        self.action_btn.setVisible(st.action is not None)
        self.action_btn.setText(st.action_label)
        self.action_btn.setEnabled(not self.busy_text)
        # Errors and plain commits are less urgent than conflicts or syncing: quieter button.
        variant = "secondary" if st.action in ("remove", "commit") else "primary"
        if self.action_btn.property("variant") != variant:
            self.action_btn.setProperty("variant", variant)
            self.action_btn.style().unpolish(self.action_btn)
            self.action_btn.style().polish(self.action_btn)

    def _elide(self) -> None:
        """Fit the path and last commit subject to the card's width (cheap; safe on resize)."""
        s = self.summary
        width = max(self.width() - 140, 160)
        self.path.setText(self.path.fontMetrics().elidedText(s.path.replace(str(Path.home()), "~"), Qt.TextElideMode.ElideMiddle, width))
        if s.last_commit and not s.error:
            self.last.setText(self.last.fontMetrics().elidedText(s.last_commit.subject, Qt.TextElideMode.ElideRight, max(self.width() - 40, 160)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()


# ---------------------------------------------------------------------- window


class DashboardWindow(QWidget):
    """Every repository you track, with branch, changes and sync state at a glance."""

    open_repo = Signal(str)  # kept for callers that only open
    open_repo_page = Signal(str, str)  # (path, page)
    SETTINGS_KEY = "dashboard/repos"
    FETCHED_KEY = "dashboard/last_fetch"

    def __init__(self, runner: TaskRunner, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("ChangesPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("GitOps · Repositories")
        self.resize(1200, 760)
        self.runner = runner
        self.settings = QSettings()
        self.task: Task | None = None
        self.fetch_task: Task | None = None
        self.cards: dict[str, RepoCard] = {}
        self.filter = "all"

        # ---- header
        self.title = QLabel("Repositories", objectName="Title")
        self.title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.subtitle = QLabel(objectName="Muted")
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        self.search = QLineEdit(objectName="SearchField")
        self.search.setPlaceholderText("Filter by name or branch")
        self.search.setAccessibleName("Filter repositories")
        self.search.setMinimumWidth(220)
        self.search.setMaximumWidth(300)
        self.search.textChanged.connect(lambda _t: self._apply_filter())
        scan = QPushButton("Scan folder…")
        add = QPushButton("Add repository…")
        for b in (scan, add):
            b.setProperty("variant", "secondary")
        scan.clicked.connect(self._scan)
        add.clicked.connect(self._add)
        self.fetch_btn = QPushButton("Fetch all")
        self.fetch_btn.setProperty("variant", "primary")
        self.fetch_btn.clicked.connect(self.fetch_all)
        header = QFrame(objectName="DiffHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(28, 16, 28, 16)
        hl.setSpacing(10)
        hl.addLayout(titles, 1)
        hl.addWidget(self.search)
        hl.addWidget(scan)
        hl.addWidget(add)
        hl.addWidget(self.fetch_btn)

        # ---- filters
        segmented = QFrame(objectName="Segmented")
        segmented.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        seg = QHBoxLayout(segmented)
        seg.setContentsMargins(2, 2, 2, 2)
        seg.setSpacing(2)
        self.filter_group = QButtonGroup(self)
        self.filter_buttons: dict[str, QPushButton] = {}
        for key in FILTERS:
            b = QPushButton(FILTER_LABELS[key])
            b.setCheckable(True)
            b.setProperty("variant", "segment")
            b.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            self.filter_group.addButton(b)
            self.filter_buttons[key] = b
            seg.addWidget(b)
        self.filter_buttons["all"].setChecked(True)
        filters = QHBoxLayout()
        filters.setContentsMargins(28, 16, 28, 0)
        filters.addWidget(segmented)
        filters.addStretch(1)

        # ---- cards
        self.grid_host = QWidget(objectName="ChangesPage")
        self.grid_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # The grid sits in a vertical layout with a stretch under it, so the host's maximum height
        # is unbounded. A bounded maximum fights QScrollArea's own resizing and loops inside Qt.
        host_layout = QVBoxLayout(self.grid_host)
        host_layout.setContentsMargins(28, 16, 28, 28)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(16)
        host_layout.addLayout(self.grid)
        host_layout.addStretch(1)
        # We size the card host ourselves (widgetResizable off). With Qt doing it, cards whose height
        # depends on their width made QScrollArea resize the host in a loop until the stack overflowed.
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.grid_host)
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.empty = QLabel(objectName="EmptyState")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addLayout(filters)
        layout.addWidget(self.empty, 1)
        layout.addWidget(self.scroll, 1)
        self._columns = 0
        self._laying_out = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_host)
        self._render_subtitle()
        self.apply_theme()

    # ------------------------------------------------------------------ repo list

    def repos(self) -> list[str]:
        value = self.settings.value(self.SETTINGS_KEY, None)
        if value is None:
            recent = self.settings.value("recent_repos", [])
            value = [recent] if isinstance(recent, str) else list(recent or [])
        return [value] if isinstance(value, str) else list(value)

    def set_repos(self, paths: list[str]) -> None:
        unique = list(dict.fromkeys(str(Path(p)) for p in paths))
        self.settings.setValue(self.SETTINGS_KEY, sorted(unique, key=lambda p: Path(p).name.lower()))
        self.refresh()

    def _add(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add repository")
        if path and GitBackend.is_repo(path):
            self.set_repos(self.repos() + [str(GitBackend(path).root)])
        elif path:
            self.subtitle.setText(f"{path} isn't a git repository.")

    def _scan(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Find repositories inside")
        if not folder:
            return
        found = GitBackend.find_repos(folder)
        self.set_repos(self.repos() + found)
        self.subtitle.setText(f"Found {len(found)} repositories in {folder}")

    def remove(self, path: str) -> None:
        self.set_repos([p for p in self.repos() if p != path])

    # ------------------------------------------------------------------ loading

    def refresh(self) -> None:
        if self.task:
            self.task.cancel()
        # Cards are children of the grid host, so Qt alone owns them. (Detaching them with
        # setParent(None) handed ownership to Python and they were deleted twice.)
        for card in self.cards.values():
            self.grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = {}
        paths = self.repos()
        self._render_subtitle(loading=bool(paths))
        task = Task(_summaries, paths)
        task.signals.items.connect(self._add_cards)
        task.signals.finished.connect(self._loaded)  # bound method: dropped if this window is deleted first
        self.task = self.runner.submit(task)
        self._relayout()

    def _add_cards(self, summaries: list[RepoSummary]) -> None:
        for s in summaries:
            card = RepoCard(s, self.grid_host)
            card.open_requested.connect(self._open)
            card.action_requested.connect(self._action)
            card.remove_requested.connect(self.remove)
            self.cards[s.path] = card
        self._relayout()

    def _loaded(self) -> None:
        self._render_subtitle()

    def _render_subtitle(self, loading: bool = False) -> None:
        n = len(self.repos())
        fetched = self.settings.value(self.FETCHED_KEY, "")
        when = ""
        if fetched:
            try:
                when = f" · fetched {relative_time(datetime.fromisoformat(fetched))}"
            except ValueError:
                when = ""
        self.subtitle.setText(f"{n} tracked{when}" + (" · checking…" if loading else ""))

    # ------------------------------------------------------------------ filters and layout

    def set_filter(self, key: str) -> None:
        self.filter = key
        self.filter_buttons[key].setChecked(True)
        self._relayout()

    def visible_cards(self) -> list[RepoCard]:
        query = self.search.text().strip().lower()
        out = []
        for card in self.cards.values():
            if self.filter not in card.state.groups:
                continue
            s = card.summary
            if query and query not in s.name.lower() and query not in (s.branch or "").lower():
                continue
            out.append(card)
        return out

    def _apply_filter(self) -> None:
        self._relayout()

    def _update_filter_counts(self) -> None:
        for key, button in self.filter_buttons.items():
            count = sum(1 for card in self.cards.values() if key in card.state.groups)
            button.setText(f"{FILTER_LABELS[key]}  {count}")
            button.setMinimumWidth(button.fontMetrics().horizontalAdvance(button.text()) + 30)

    def _column_count(self) -> int:
        """Columns that fit. Based on the scroll area's outer width minus a scrollbar, never the viewport:
        the viewport shrinks when a scrollbar appears, and re-laying out can make it vanish again,
        which would loop forever."""
        scrollbar = self.scroll.verticalScrollBar().sizeHint().width()
        usable = max(self.scroll.width() - scrollbar - 56, CARD_MIN_WIDTH)
        return max(1, (usable + 16) // (CARD_MIN_WIDTH + 16))

    def _relayout(self) -> None:
        if self._laying_out:
            return
        self._laying_out = True
        try:
            self._do_relayout()
        finally:
            self._laying_out = False

    def _do_relayout(self) -> None:
        self._update_filter_counts()
        for card in self.cards.values():
            self.grid.removeWidget(card)
            card.hide()
        visible = self.visible_cards()
        columns = self._column_count()
        self._columns = columns
        for i, card in enumerate(visible):
            self.grid.addWidget(card, i // columns, i % columns)
            card.show()
        for col in range(columns):
            self.grid.setColumnStretch(col, 1)
        if not self.repos():
            self.empty.setText("No repositories yet. Add one, or scan a folder to find all the repos inside it.")
        elif self.cards and not visible:
            self.empty.setText("Nothing matches this filter.")
        self.empty.setVisible((not self.repos()) or (bool(self.cards) and not visible))
        self.scroll.setVisible(not self.empty.isVisible())
        self._fit_timer.start(0)

    def _fit_host(self) -> None:
        """Give the card host the viewport's width and the height its cards need (done on the next tick)."""
        width = self.scroll.viewport().width()
        layout = self.grid_host.layout()
        layout.activate()
        height = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else layout.totalSizeHint().height()
        self.grid_host.resize(width, max(height, self.scroll.viewport().height()))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._column_count() != self._columns:
            self._relayout()
        else:
            self._fit_timer.start(0)

    # ------------------------------------------------------------------ actions

    def _open(self, path: str, page: str) -> None:
        self.open_repo_page.emit(path, page)
        self.open_repo.emit(path)

    def _action(self, path: str, action: str) -> None:
        if action == "remove":
            self.remove(path)
        elif action in ("resolve", "continue", "commit"):
            self._open(path, "changes")
        elif action in ("pull", "push"):
            self._sync(path, action)

    def _card(self, path: str) -> RepoCard | None:
        """The live card for `path`, or None (a refresh replaced it, or the window is gone).

        Background tasks look cards up by path at the moment they report, never hold one.
        """
        if not shiboken6.isValid(self):
            return None
        card = self.cards.get(path)
        return card if card is not None and shiboken6.isValid(card) else None

    def _sync(self, path: str, action: str) -> None:
        card = self._card(path)
        if card is None:
            return
        card.set_busy("Pulling…" if action == "pull" else "Pushing…")
        task = Task(_sync_repo, path, action)
        errors: list[str] = []
        task.signals.items.connect(lambda lines, p=path: self._progress(p, lines[-1]))
        task.signals.error.connect(errors.append)
        task.signals.finished.connect(lambda p=path: self._resummarize(p, errors[0] if errors else None))
        self.runner.submit(task)

    def _progress(self, path: str, line: str) -> None:
        card = self._card(path)
        if card:
            card.set_busy(line[:120])

    def _resummarize(self, path: str, error: str | None) -> None:
        card = self._card(path)
        if card is None:
            return
        card.update_summary(GitBackend.summarize_repo(path))
        if error:
            detail = error.split("): ", 1)[-1].strip().splitlines()
            reason = next((line for line in detail if "hint:" not in line and line.strip()), "failed")
            card.set_note(f"Failed: {reason[:160]}")
        self._relayout()

    def fetch_all(self) -> None:
        if self.fetch_task is not None:
            self.fetch_task.cancel()
            return
        paths = self.repos()
        if not paths:
            return
        self.fetch_btn.setText("Cancel")
        for path in paths:
            if path in self.cards:
                self.cards[path].set_busy("Waiting to fetch…")
        task = Task(_fetch_all, paths)
        task.signals.items.connect(self._fetch_progress)
        task.signals.finished.connect(self._fetch_done)
        self.fetch_task = self.runner.submit(task)

    def _fetch_progress(self, batch: list[tuple[str, str]]) -> None:
        latest: dict[str, str] = {}
        for path, line in batch:
            latest[path] = line
        for path, line in latest.items():
            card = self._card(path)
            if card:
                card.set_busy(f"Fetching: {line[:100]}" if line not in ("done", "no remote") else line.capitalize())

    def _fetch_done(self) -> None:
        if not shiboken6.isValid(self):
            return
        cancelled = self.fetch_task is not None and self.fetch_task.cancelled
        self.fetch_task = None
        self.fetch_btn.setText("Fetch all")
        if not cancelled:
            self.settings.setValue(self.FETCHED_KEY, datetime.now(timezone.utc).isoformat())
        self.refresh()

    def apply_theme(self) -> None:
        for card in self.cards.values():
            card.render()
        self.fetch_btn.setIcon(icons.icon("sync", "#FFFFFF", 16))
