import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402

from gitops.core.models import Branch, Stash, Tag  # noqa: E402
from gitops.ui.main_window import MainWindow  # noqa: E402
from gitops.ui.widgets.branches_view import actions_for, build_sections, tracking_chip  # noqa: E402
from gitops.ui.widgets.stashes_view import split_message, stash_row  # noqa: E402


@pytest.fixture
def window(qtbot, qapp, tmp_path, repo):
    qapp.setOrganizationName("gitops-tests")
    qapp.setApplicationName("gitops-tests")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    repo.write("a.txt", "a\n")
    repo.commit("first")
    w = MainWindow()
    qtbot.addWidget(w)
    w.open_repo(str(repo.path))
    qtbot.waitUntil(lambda: w.history.model.rowCount() == 1, timeout=5000)
    return w


def branch(name, **kw):
    return Branch(name, kw.pop("is_remote", False), kw.pop("is_current", False), "a" * 40, **kw)


def test_tracking_chip():
    assert tracking_chip(branch("x")) == ("Not published", "muted")
    assert tracking_chip(branch("x", upstream="origin/x")) == ("Up to date", "success")
    assert tracking_chip(branch("x", upstream="origin/x", ahead=2)) == ("↑2 to push", "accent")
    assert tracking_chip(branch("x", upstream="origin/x", behind=3)) == ("↓3 behind", "warning")
    assert tracking_chip(branch("x", upstream="origin/x", ahead=1, behind=1)) == ("↑1 ↓1", "warning")
    assert tracking_chip(branch("x", upstream="origin/x", upstream_gone=True)) == ("Upstream gone", "danger")


def test_sections_group_remotes_and_filter():
    branches = [
        branch("zeta"), branch("main", is_current=True, upstream="origin/main"),
        branch("origin/HEAD", is_remote=True), branch("origin/main", is_remote=True),
        branch("upstream/dev", is_remote=True),
    ]
    tags = [Tag("v1", "b" * 40, True, "first release")]
    sections = dict(build_sections(branches, tags, [], [], None))
    assert [r.title for r in sections["Local branches"]] == ["main", "zeta"]  # current first
    assert [r.title for r in sections["Remote · origin"]] == ["main"]  # origin/HEAD hidden
    assert [r.title for r in sections["Remote · upstream"]] == ["dev"]
    assert sections["Tags"][0].subtitle == "first release"

    filtered = dict(build_sections(branches, tags, [], [], None, "zet"))
    assert [r.title for r in filtered["Local branches"]] == ["zeta"]
    assert filtered["Tags"] == []


def test_actions_depend_on_row():
    sections = dict(build_sections([branch("main", is_current=True), branch("topic"), branch("origin/x", is_remote=True)], [], [], [], None))
    current, topic = sections["Local branches"]
    keys = lambda row: [a.key for a in actions_for(row, "main", ["origin"], None)]  # noqa: E731
    assert keys(topic) == ["checkout", "merge", "rebase", "new_branch", "show", "rename", "delete"]
    assert "checkout" not in keys(current)
    assert next(a for a in actions_for(current, "main", [], None) if a.key == "delete").enabled is False
    assert keys(sections["Remote · origin"][0])[0] == "checkout_remote"
    tag_rows = dict(build_sections([], [Tag("v1", "b" * 40, False)], [], [], None))["Tags"]
    assert keys(tag_rows[0]) == ["show", "new_branch", "push_tag:origin", "delete_tag"]


def test_stash_message_parsing():
    assert split_message("On main: my wip") == ("main", "my wip")
    assert split_message("WIP on feature/x: abc1234 subject") == ("feature/x", "Work in progress on “subject”")
    assert split_message("custom") == (None, "custom")
    row = stash_row(Stash("stash@{0}", "c" * 40, 0, "On main: my wip"))
    assert row.title == "my wip" and row.subtitle.startswith("stash@{0} · on main")


def test_branch_page_actions_run(window, qtbot):
    window.backend.create_branch("topic", checkout=False)
    window.refresh()
    page = window.branches
    qtbot.waitUntil(lambda: page.list.select("local:topic"), timeout=5000)
    assert page.detail_status.text() == "Only on this computer: not pushed to a remote yet."
    assert page.buttons_box.itemAt(0).widget().text() == "Switch to topic"

    page.buttons_box.itemAt(0).widget().click()
    assert window.backend.current_branch() == "topic"
    qtbot.waitUntil(lambda: (r := page.list.current()) is not None and r.key == "local:topic" and r.current, timeout=5000)


def test_stashes_page_empty_state(window, qtbot):
    window.show_page(window.stashes)
    assert window.stashes.list_stack.currentWidget() is window.stashes.list_empty
    assert window.stashes.preview_stack.currentWidget() is window.stashes.empty
