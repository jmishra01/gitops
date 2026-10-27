"""The redesigned repositories dashboard: card states, filters, actions, and layout stability."""

import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402

from gitops.core.models import RepoSummary  # noqa: E402
from gitops.services.task_runner import TaskRunner  # noqa: E402
from gitops.ui.dashboard import DashboardWindow, card_state  # noqa: E402


def summary(**kw) -> RepoSummary:
    base = dict(path="/r", name="r", branch="main", changed=0, conflicts=0, ahead_behind=(0, 0), stashes=0, last_commit=None, operation=None)
    base.update(kw)
    return RepoSummary(**base)


@pytest.mark.parametrize(
    "kw, kind, chip, action, groups",
    [
        (dict(error="boom"), "error", "Unavailable", "Remove", {"all", "attention"}),
        (dict(conflicts=2, operation="merge"), "conflict", "2 conflicts", "Resolve", {"all", "attention"}),
        (dict(operation="rebase"), "busy", "Rebase paused", "Continue", {"all", "attention"}),
        (dict(ahead_behind=(0, 3)), "behind", "Behind 3", "Pull 3", {"all", "behind"}),
        (dict(ahead_behind=(2, 1), changed=4), "changes", "4 changes", "Pull 1", {"all", "attention", "behind"}),
        (dict(ahead_behind=(2, 0), changed=6), "changes", "6 changes", "Push 2", {"all", "attention"}),
        (dict(changed=1), "changes", "1 change", "Commit…", {"all", "attention"}),
        (dict(ahead_behind=(2, 0)), "ahead", "Ahead 2", "Push 2", {"all", "attention"}),
        (dict(ahead_behind=None), "clean", "No upstream", "", {"all", "clean"}),
        (dict(), "clean", "Up to date", "", {"all", "clean"}),
    ],
)
def test_card_state(kw, kind, chip, action, groups):
    st = card_state(summary(**kw))
    assert (st.kind, st.chip, st.action_label, st.groups) == (kind, chip, action, groups)


def run(*args, cwd=None):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@x", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path):
    """behind: one commit behind its remote. ahead: one unpushed commit. clean: up to date."""
    remote = tmp_path / "remote.git"
    run("init", "-q", "--bare", "-b", "main", str(remote))
    paths = {}
    for name in ("behind", "ahead", "clean"):
        path = tmp_path / name
        run("clone", "-q", str(remote), str(path))
        paths[name] = path
    (paths["clean"] / "f.txt").write_text("1\n")
    run("add", "-A", cwd=paths["clean"])
    run("commit", "-qm", "one", cwd=paths["clean"])
    run("push", "-q", "-u", "origin", "main", cwd=paths["clean"])
    for name in ("behind", "ahead"):
        run("pull", "-q", "origin", "main", cwd=paths[name])
        run("branch", "-q", "--set-upstream-to=origin/main", cwd=paths[name])
    (paths["ahead"] / "g.txt").write_text("2\n")
    run("add", "-A", cwd=paths["ahead"])
    run("commit", "-qm", "two", cwd=paths["ahead"])
    (paths["clean"] / "f.txt").write_text("3\n")
    run("commit", "-qam", "three", cwd=paths["clean"])
    run("push", "-q", cwd=paths["clean"])
    run("fetch", "-q", cwd=paths["behind"])
    run("fetch", "-q", cwd=paths["ahead"])
    run("config", "pull.rebase", "true", cwd=paths["ahead"])  # how this user reconciles diverged branches
    return paths


@pytest.fixture
def dash(qtbot, qapp, tmp_path, repos):
    qapp.setOrganizationName("gitops-dash-tests")
    qapp.setApplicationName("gitops-dash-tests")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    d = DashboardWindow(TaskRunner())
    qtbot.addWidget(d)
    d.resize(1200, 800)
    d.show()
    d.set_repos([str(p) for p in repos.values()] + [str(tmp_path / "gone")])
    qtbot.waitUntil(lambda: len(d.cards) == 4, timeout=10000)
    return d


def test_filters_counts_and_search(dash, repos):
    chips = {Path(p).name: c.state.chip for p, c in dash.cards.items()}
    assert chips == {"behind": "Behind 1", "ahead": "Behind 1", "clean": "Up to date", "gone": "Unavailable"}
    assert dash.filter_buttons["behind"].text().endswith("2")
    dash.set_filter("behind")
    assert {Path(c.summary.path).name for c in dash.visible_cards()} == {"behind", "ahead"}
    dash.set_filter("all")
    dash.search.setText("clea")
    assert [c.summary.name for c in dash.visible_cards()] == ["clean"]
    dash.search.clear()
    dash.set_filter("clean")
    assert not dash.empty.isVisible() or not dash.cards
    dash.search.setText("zzz")
    assert dash.empty.text() == "Nothing matches this filter."


def test_pull_then_push_from_cards(dash, repos, qtbot):
    behind = str(repos["behind"])
    dash.cards[behind].action_btn.click()  # "Pull 1"
    qtbot.waitUntil(lambda: dash.cards[behind].state.chip == "Up to date", timeout=10000)

    ahead = str(repos["ahead"])
    assert dash.cards[ahead].state.action == "pull"  # pull first, before pushing
    dash.cards[ahead].action_btn.click()
    qtbot.waitUntil(lambda: dash.cards[ahead].state.action == "push", timeout=10000)
    dash.cards[ahead].action_btn.click()  # now "Push 1"
    qtbot.waitUntil(lambda: dash.cards[ahead].state.chip == "Up to date", timeout=10000)


def test_remove_unavailable_repo(dash, tmp_path, qtbot):
    gone = str(tmp_path / "gone")
    assert dash.cards[gone].open_btn.isHidden()
    dash.cards[gone].action_btn.click()  # "Remove"
    qtbot.waitUntil(lambda: len(dash.cards) == 3, timeout=10000)
    assert gone not in dash.repos()


def test_open_routes_to_page(dash, repos):
    opened = []
    dash.open_repo_page.connect(lambda path, page: opened.append((Path(path).name, page)))
    dash.cards[str(repos["clean"])].open_btn.click()
    dash._action(str(repos["ahead"]), "resolve")
    assert opened == [("clean", "changes"), ("ahead", "changes")]


def test_fetch_all_records_time(dash, qtbot):
    assert "fetched" not in dash.subtitle.text()
    dash.fetch_all()
    assert dash.fetch_btn.text() == "Cancel"
    qtbot.waitUntil(lambda: dash.fetch_task is None, timeout=15000)
    qtbot.waitUntil(lambda: "fetched" in dash.subtitle.text(), timeout=10000)


def test_layout_survives_resizes_and_filters(dash, qtbot, qapp):
    """Regression: resizing and filtering once overflowed the stack inside QScrollArea."""
    seen = set()
    for width in range(560, 1500, 40):
        for height in (380, 640, 900):
            dash.resize(width, height)
            qapp.processEvents()
            dash.set_filter(("attention", "behind", "clean", "all")[(width // 40 + height) % 4])
            qapp.processEvents()
            seen.add(dash._columns)
    assert {1, 2, 3}.issubset(seen)


def test_diverged_pull_without_config_explains_failure(dash, repos, qtbot):
    ahead = str(repos["ahead"])
    run("config", "--unset", "pull.rebase", cwd=repos["ahead"])
    dash.cards[ahead].action_btn.click()
    qtbot.waitUntil(lambda: dash.cards[ahead].note.startswith("Failed:"), timeout=10000)
    assert dash.cards[ahead].action_btn.isEnabled()  # a failure must not lock the button
