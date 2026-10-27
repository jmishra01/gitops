"""The redesigned conflict resolver: several files, per-conflict decisions, undo, special files."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402

from gitops.core import GitBackend  # noqa: E402
from gitops.core.conflicts import Choice  # noqa: E402
from gitops.ui.dialogs import conflict_editor  # noqa: E402
from gitops.ui.dialogs.conflict_editor import ConflictResolver  # noqa: E402

BASE = "".join(f"line {n}\n" for n in range(1, 31))


def variant(**edits) -> str:
    lines = BASE.splitlines()
    for key, value in edits.items():
        lines[int(key[1:]) - 1] = value
    return "\n".join(lines) + "\n"


@pytest.fixture
def merge(repo, qapp, tmp_path):
    """Two files conflict: a.txt twice (lines 3 and 25), b.txt once; c.bin is a binary conflict."""
    qapp.setOrganizationName("gitops-resolver-tests")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    repo.write("a.txt", BASE)
    repo.write("b.txt", BASE)
    (repo.path / "c.bin").write_bytes(b"\0base")
    repo.commit("base")
    repo.git("switch", "-q", "-c", "feature")
    repo.write("a.txt", variant(l3="THEIRS 3", l25="THEIRS 25"))
    repo.write("b.txt", variant(l10="THEIRS 10"))
    (repo.path / "c.bin").write_bytes(b"\0theirs")
    repo.commit("feature")
    repo.git("switch", "-q", "main")
    repo.write("a.txt", variant(l3="OURS 3", l25="OURS 25"))
    repo.write("b.txt", variant(l10="OURS 10"))
    (repo.path / "c.bin").write_bytes(b"\0ours")
    repo.commit("main")
    backend = GitBackend(repo.path)
    assert sorted(backend.merge("feature")) == ["a.txt", "b.txt", "c.bin"]
    return repo, backend


def test_header_progress_and_rows(merge, qtbot):
    repo, backend = merge
    r = ConflictResolver(backend, "a.txt")
    qtbot.addWidget(r)
    assert r.title.text().startswith("Merging · Merge branch 'feature'")
    assert r.subtitle.text().startswith("0 of 3 files resolved")
    assert r.finish_btn.text() == "Commit merge"
    assert r.current.conflict_count == 2 and "Conflict 1 of 2 · 2 left" in r.counter.text()
    assert len(r.conflict_rows) == 2
    # 30 shared lines around the two conflicts: the long middle run is folded.
    folds = [w for w in r.rows_host.findChildren(conflict_editor.QPushButton) if "unchanged lines" in w.text()]
    assert folds and "unchanged lines" in folds[0].text()


def test_decide_undo_and_hand_edit(merge, qtbot):
    repo, backend = merge
    r = ConflictResolver(backend, "a.txt")
    qtbot.addWidget(r)
    r.decide(0, Choice.THEIRS)
    assert r.focus == 1 and "1 left" in r.counter.text()  # moved on to the next undecided conflict
    r.undo(0)
    assert r.current.undecided == [0, 1]
    r.decide(0, Choice.BOTH)
    r.decide(1, "hand typed")
    text = r.result_text()
    assert "OURS 3\nTHEIRS 3\n" in text and "hand typed\n" in text and "<<<<<<<" not in text
    assert text.count("\n") == 31  # 30 lines, one conflict now two lines


def test_take_all_and_mark_resolved_moves_to_next_file(merge, qtbot):
    repo, backend = merge
    r = ConflictResolver(backend, "a.txt")
    qtbot.addWidget(r)
    r.decide_all(Choice.OURS)
    assert r.mark_btn.property("variant") == "primary"
    r.mark_resolved()
    assert (repo.path / "a.txt").read_text() == variant(l3="OURS 3", l25="OURS 25")
    assert r.current.path != "a.txt"  # jumped to the next unresolved file
    assert r.subtitle.text().startswith("1 of 3 files resolved") and r.progress.value() == 1


def test_undecided_warns_before_saving(merge, qtbot, monkeypatch):
    repo, backend = merge
    asked = []
    monkeypatch.setattr(
        conflict_editor.QMessageBox,
        "warning",
        lambda *a, **k: asked.append(a[2]) or conflict_editor.QMessageBox.StandardButton.Cancel,
    )
    r = ConflictResolver(backend, "b.txt")
    qtbot.addWidget(r)
    r.mark_resolved()
    assert asked and "no decision" in asked[0]
    assert "b.txt" in backend.conflicted_paths()


def test_binary_file_is_a_whole_file_choice(merge, qtbot):
    repo, backend = merge
    r = ConflictResolver(backend, "c.bin")
    qtbot.addWidget(r)
    assert r.current.special == "binary" and r.body.currentWidget() is r.special
    keep_theirs = [b for b in r.special.findChildren(conflict_editor.QPushButton) if b.text() == "Keep theirs"][0]
    keep_theirs.click()
    assert (repo.path / "c.bin").read_bytes() == b"\0theirs"
    assert "c.bin" not in backend.conflicted_paths()


def test_whole_file_mode_round_trip(merge, qtbot):
    repo, backend = merge
    r = ConflictResolver(backend, "b.txt")
    qtbot.addWidget(r)
    r.raw_btn.setChecked(True)
    assert r.body.currentWidget() is r.raw and "<<<<<<<" in r.raw.toPlainText()
    r.raw.setPlainText(variant(l10="typed in raw mode"))
    r.raw_btn.setChecked(False)  # no markers left, so no conflicts left
    assert r.current.conflict_count == 0 and r.result_text() == variant(l10="typed in raw mode")


def test_finish_requires_every_file_then_commits(merge, qtbot, monkeypatch):
    repo, backend = merge
    infos = []
    monkeypatch.setattr(conflict_editor.QMessageBox, "information", lambda *a, **k: infos.append(a[2]))
    r = ConflictResolver(backend, "a.txt")
    qtbot.addWidget(r)
    r._finish()
    assert infos and "3 remaining" in infos[0] and r.outcome is None
    for path in ("a.txt", "b.txt"):
        r.open_file(path)
        r.decide_all(Choice.THEIRS)
        r.mark_resolved()
    r.open_file("c.bin")
    [b for b in r.special.findChildren(conflict_editor.QPushButton) if b.text() == "Keep ours"][0].click()
    assert backend.conflicted_paths() == [] and r.finish_btn.property("variant") == "primary"
    r._finish()
    assert r.outcome == "commit"


def test_abort_outcome(merge, qtbot):
    repo, backend = merge
    r = ConflictResolver(backend, "a.txt")
    qtbot.addWidget(r)
    r._abort()
    assert r.outcome == "abort"
