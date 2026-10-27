"""A full-page file finder: type part of a name or path, pick from the list. Used by the File inspector."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QStandardItem
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from gitops.ui.widgets.row_list import ROW_ROLE, Header, Row, RowList

MAX_RESULTS = 300
MAX_RECENT = 8


def rank_files(files: list[str], query: str, limit: int = MAX_RESULTS) -> list[str]:
    """Best matches first: file name starts with the query, then name contains it, then path contains it,
    then the query's characters appear in order (so "srclog" finds src/auth/login.py)."""
    q = query.strip().lower()
    if not q:
        return files[:limit]
    scored: list[tuple[int, int, str]] = []
    for path in files:
        p = path.lower()
        name = p.rsplit("/", 1)[-1]
        if name.startswith(q):
            score = 0
        elif q in name:
            score = 1
        elif q in p:
            score = 2
        elif _in_order(q.replace(" ", ""), p):
            score = 3
        else:
            continue
        scored.append((score, len(path), path))
    scored.sort()
    return [path for _score, _len, path in scored[:limit]]


def _in_order(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(ch in it for ch in needle)


def file_row(path: str) -> Row:
    folder, _, name = path.rpartition("/")
    return Row(key=path, kind="file", title=name, subtitle=folder or "Repository root", icon="file")


class FileBrowser(QWidget):
    """Search box plus the matching tracked files. Enter or a click opens the file."""

    file_chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, objectName="ChangesPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.files: list[str] = []
        self.recent: list[str] = []

        self.heading = QLabel("Open a file", objectName="Title")
        self.hint = QLabel(objectName="Muted")
        self.hint.setWordWrap(True)
        self.search = QLineEdit(objectName="SearchField")
        self.search.setPlaceholderText("Type part of a file name or path…")
        self.search.setAccessibleName("Find a file")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self._open_current)
        self.search.installEventFilter(self)
        self.count = QLabel(objectName="Muted")

        self.list = RowList()
        self.list.clicked.connect(self._clicked)

        column = QWidget()
        column.setMaximumWidth(760)
        cl = QVBoxLayout(column)
        cl.setContentsMargins(0, 28, 0, 16)
        cl.setSpacing(8)
        cl.addWidget(self.heading)
        cl.addWidget(self.hint)
        cl.addSpacing(6)
        cl.addWidget(self.search)
        cl.addWidget(self.count)
        cl.addWidget(self.list, 1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.addStretch(1)
        layout.addWidget(column, 4)
        layout.addStretch(1)
        self.set_purpose("history")

    def set_purpose(self, tab: str) -> None:
        self.hint.setText(
            "Pick a file to see who last changed each line, and in which commit."
            if tab == "blame"
            else "Pick a file to see its whole story: every version, who changed what, and how it compares."
        )

    def set_files(self, files: list[str]) -> None:
        self.files = sorted(files)
        self.recent = [p for p in self.recent if p in set(self.files)]
        self._refresh()

    def remember(self, path: str) -> None:
        self.recent = [path] + [p for p in self.recent if p != path][: MAX_RECENT - 1]
        self._refresh()

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _refresh(self) -> None:
        query = self.search.text()
        model = self.list.model_
        model.clear()
        if not self.files:
            self.count.setText("Loading files…")
            return
        sections: list[tuple[str, list[str]]] = []
        if query.strip():
            matches = rank_files(self.files, query)
            sections.append(("Matches", matches))
            total = len(matches)
        else:
            if self.recent:
                sections.append(("Recent", self.recent))
            sections.append(("All files", self.files[:MAX_RESULTS]))
            total = len(self.files)
        for title, paths in sections:
            if not paths:
                continue
            header = QStandardItem()
            header.setData(Header(title, len(paths)), ROW_ROLE)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            model.appendRow(header)
            for path in paths:
                it = QStandardItem()
                it.setData(file_row(path), ROW_ROLE)
                it.setToolTip(path)
                model.appendRow(it)
        if query.strip():
            self.count.setText(
                "No files match." if not total else f"{total}{'+' if total >= MAX_RESULTS else ''} matching files"
            )
        else:
            shown = "" if total <= MAX_RESULTS else f" (showing the first {MAX_RESULTS}; type to narrow)"
            self.count.setText(f"{total} tracked files{shown}")
        self._select_first()

    def _rows(self) -> list[int]:
        model = self.list.model_
        return [r for r in range(model.rowCount()) if isinstance(model.item(r).data(ROW_ROLE), Row)]

    def _select_first(self) -> None:
        rows = self._rows()
        if rows:
            self.list.setCurrentIndex(self.list.model_.index(rows[0], 0))

    def _move(self, step: int) -> None:
        rows = self._rows()
        if not rows:
            return
        current = self.list.currentIndex().row()
        pos = rows.index(current) if current in rows else -1
        target = rows[max(0, min(len(rows) - 1, pos + step))]
        index = self.list.model_.index(target, 0)
        self.list.setCurrentIndex(index)
        self.list.scrollTo(index)

    def _open_current(self) -> None:
        row = self.list.currentIndex().data(ROW_ROLE)
        if isinstance(row, Row):
            self.file_chosen.emit(row.key)
        elif self.search.text().strip() in self.files:
            self.file_chosen.emit(self.search.text().strip())

    def _clicked(self, index) -> None:
        row = index.data(ROW_ROLE)
        if isinstance(row, Row):
            self.file_chosen.emit(row.key)

    def eventFilter(self, obj, event) -> bool:
        # Arrow keys in the search box move through the list, so the keyboard never has to leave it.
        if obj is self.search and isinstance(event, QKeyEvent) and event.type() == QKeyEvent.Type.KeyPress:
            steps = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: 10, Qt.Key.Key_PageUp: -10}
            if event.key() in steps:
                self._move(steps[event.key()])
                return True
        return super().eventFilter(obj, event)
