import pytest

from gitops.core import GitBackend
from gitops.core.rebase import build_todo, has_merges, rebase_plan, run_interactive_rebase


@pytest.fixture
def linear(repo):
    """base, then commits a, b, c each adding a file."""
    repo.write("base.txt", "base\n")
    base = repo.commit("base")
    for name in "abc":
        repo.write(f"{name}.txt", f"{name}\n")
        repo.commit(f"add {name}\n\nbody of {name}")
    return repo, GitBackend(repo.path), base


def subjects(backend):
    return [c.subject for c in backend.log()]


def test_cherry_pick_and_revert(repo):
    repo.write("a.txt", "a\n")
    repo.commit("base")
    repo.git("switch", "-q", "-c", "side")
    repo.write("b.txt", "b\n")
    picked = repo.commit("side work")
    repo.git("switch", "-q", "main")
    backend = GitBackend(repo.path)

    assert backend.cherry_pick(picked) == []
    assert subjects(backend)[0] == "side work"
    assert backend.revert(backend.log()[0].sha) == []
    assert subjects(backend)[0].startswith('Revert "side work"')
    assert not (repo.path / "b.txt").exists()


def test_cherry_pick_conflict_then_abort(repo):
    repo.write("a.txt", "a\n")
    repo.commit("base")
    repo.git("switch", "-q", "-c", "side")
    repo.write("a.txt", "side\n")
    picked = repo.commit("side edit")
    repo.git("switch", "-q", "main")
    repo.write("a.txt", "main\n")
    repo.commit("main edit")
    backend = GitBackend(repo.path)

    assert backend.cherry_pick(picked) == ["a.txt"]
    assert backend.operation_in_progress() == "cherry-pick"
    backend.abort_operation()
    assert backend.operation_in_progress() is None
    assert (repo.path / "a.txt").read_text() == "main\n"


def test_cherry_pick_conflict_then_continue(repo):
    repo.write("a.txt", "a\n")
    repo.commit("base")
    repo.git("switch", "-q", "-c", "side")
    repo.write("a.txt", "side\n")
    picked = repo.commit("side edit")
    repo.git("switch", "-q", "main")
    repo.write("a.txt", "main\n")
    repo.commit("main edit")
    backend = GitBackend(repo.path)

    backend.cherry_pick(picked)
    backend.mark_resolved("a.txt", b"resolved\n")
    assert backend.continue_operation() == []
    assert backend.operation_in_progress() is None
    assert subjects(backend)[0] == "side edit"


def test_reset_modes(linear):
    repo, backend, base = linear
    head_before = backend.log()[0].sha
    backend.reset(base, "soft")
    assert subjects(backend) == ["base"]
    assert {s.path for s in backend.status() if s.is_staged} == {"a.txt", "b.txt", "c.txt"}
    backend.reset(head_before, "hard")
    backend.reset(base, "mixed")
    assert not [s for s in backend.status() if s.is_staged]
    backend.reset(base, "hard")
    # --hard resets tracked files only: after the mixed reset they became untracked, so they stay.
    assert {(s.path, s.index) for s in backend.status()} == {("a.txt", "?"), ("b.txt", "?"), ("c.txt", "?")}
    with pytest.raises(ValueError):
        backend.reset(base, "weird")


def test_tags_create_delete(linear):
    _, backend, base = linear
    backend.create_tag("v1", base, message="First")
    backend.create_tag("light")
    assert {t.name for t in backend.tags()} == {"v1", "light"}
    backend.delete_tag("light")
    assert [t.name for t in backend.tags()] == ["v1"]


def test_blame(repo):
    repo.write("f.txt", "one\ntwo\n")
    first = repo.commit("first", author="Alice")
    repo.write("f.txt", "one\nTWO\nthree\n")
    second = repo.commit("second", author="Bob")
    repo.write("f.txt", "one\nTWO\nthree\nuncommitted\n")
    backend = GitBackend(repo.path)

    lines = backend.blame("f.txt")
    assert [(ln.line_number, ln.sha, ln.author) for ln in lines[:3]] == [
        (1, first, "Alice"),
        (2, second, "Bob"),
        (3, second, "Bob"),
    ]
    assert lines[3].uncommitted and lines[3].text == "uncommitted"
    assert lines[1].summary == "second" and lines[1].text == "TWO"

    at_first = backend.blame("f.txt", first)
    assert [ln.text for ln in at_first] == ["one", "two"]


def test_search_commits(linear):
    _, backend, base = linear
    assert [c.subject for c in backend.search_commits("ADD B")] == ["add b"]
    assert [c.subject for c in backend.search_commits("body of")] == ["add c", "add b", "add a"]
    assert len(backend.search_commits("author:alice")) == 4
    assert backend.search_commits(base[:8])[0].sha == base
    assert backend.search_commits("nothing matches this") == []


# ---------------------------------------------------------------- interactive rebase


def test_plan_lists_commits_oldest_first(linear):
    _, backend, base = linear
    plan = rebase_plan(backend, base)
    assert [s.commit.subject for s in plan] == ["add a", "add b", "add c"]
    assert not has_merges(backend, base)
    assert [s.commit.subject for s in rebase_plan(backend, None)][0] == "base"


def test_reorder_drop_reword(linear):
    repo, backend, base = linear
    a, b, c = rebase_plan(backend, base)
    b.action = "drop"
    c.action = "reword"
    c.message = "Add c, reworded"
    assert run_interactive_rebase(backend, base, [c, a, b]) == []
    assert subjects(backend) == ["add a", "Add c, reworded", "base"]
    assert not (repo.path / "b.txt").exists()
    assert backend.operation_in_progress() is None


def test_squash_combines_messages_and_fixup_discards(linear):
    _, backend, base = linear
    a, b, c = rebase_plan(backend, base)
    b.action = "squash"
    c.action = "fixup"
    assert run_interactive_rebase(backend, base, [a, b, c]) == []
    log = backend.log()
    assert [c.subject for c in log] == ["add a", "base"]
    message = backend.last_commit_message()
    assert "body of a" in message and "add b" in message and "add c" not in message
    files = backend.run("show", "--name-only", "--format=", "HEAD").split()
    assert sorted(files) == ["a.txt", "b.txt", "c.txt"]


def test_first_step_cannot_be_squash(linear, tmp_path):
    _, backend, base = linear
    a, b, c = rebase_plan(backend, base)
    a.action = "squash"
    with pytest.raises(ValueError):
        build_todo(backend, [a, b, c], tmp_path)


def test_rebase_conflict_then_resolve_and_continue(repo):
    repo.write("f.txt", "base\n")
    base = repo.commit("base")
    repo.write("f.txt", "one\n")
    repo.commit("one")
    repo.write("f.txt", "two\n")
    repo.commit("two")
    backend = GitBackend(repo.path)
    one, two = rebase_plan(backend, base)
    two.action = "reword"
    two.message = "two, reworded"

    conflicts = run_interactive_rebase(backend, base, [two, one])  # swapping edits to one line conflicts
    assert conflicts == ["f.txt"]
    assert backend.operation_in_progress() == "rebase"
    backend.mark_resolved("f.txt", b"two\n")
    remaining = backend.continue_operation()
    while remaining:
        backend.mark_resolved("f.txt", b"one-then-two\n")
        remaining = backend.continue_operation()
    assert backend.operation_in_progress() is None
    assert subjects(backend)[0] == "one"
    assert "two, reworded" in subjects(backend)


def test_rebase_onto_branch(repo):
    repo.write("a.txt", "a\n")
    repo.commit("base")
    repo.git("switch", "-q", "-c", "feature")
    repo.write("f.txt", "f\n")
    repo.commit("feature work")
    repo.git("switch", "-q", "main")
    repo.write("m.txt", "m\n")
    repo.commit("main work")
    repo.git("switch", "-q", "feature")
    backend = GitBackend(repo.path)
    assert backend.rebase_onto("main") == []
    assert subjects(backend) == ["feature work", "main work", "base"]


def test_reflog_and_restore_undo_a_rebase(linear):
    repo, backend, base = linear
    before = backend.log()[0].sha
    a, b, c = rebase_plan(backend, base)
    b.action = "drop"
    run_interactive_rebase(backend, base, [a, b, c])
    assert "add b" not in subjects(backend)

    entries = backend.reflog()
    assert entries[0].selector == "main@{0}" and "rebase" in entries[0].action
    assert entries[1].sha == before  # one step back is the pre-rebase tip

    repo.write("base.txt", "uncommitted edit\n")  # a local change unrelated to the rewrite survives
    backend.restore_to(entries[1].sha)
    assert subjects(backend)[:3] == ["add c", "add b", "add a"]
    assert (repo.path / "base.txt").read_text() == "uncommitted edit\n"


def test_restore_refuses_to_clobber_local_changes(linear):
    repo, backend, base = linear
    repo.write("c.txt", "local edit\n")
    from gitops.core import GitError

    with pytest.raises(GitError):
        backend.restore_to(base)  # c.txt must change but has local edits
    assert (repo.path / "c.txt").read_text() == "local edit\n"
