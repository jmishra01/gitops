import pytest

from gitops.core import GitBackend
from gitops.core.compare import unified_diff
from gitops.core.conflicts import Choice, has_conflict_markers, parse_conflicts, resolve, resolve_all


@pytest.fixture
def diverged(repo):
    """main and feature both edit line 2 of f.txt; feature also adds g.txt."""
    repo.write("f.txt", "one\ntwo\nthree\n")
    repo.commit("base")
    repo.git("switch", "-q", "-c", "feature")
    repo.write("f.txt", "one\nTWO-feature\nthree\n")
    repo.write("g.txt", "new\n")
    repo.commit("feature edit")
    repo.git("switch", "-q", "main")
    repo.write("f.txt", "one\nTWO-main\nthree\n")
    repo.commit("main edit")
    return repo, GitBackend(repo.path)


def test_clean_merge(repo):
    repo.write("a.txt", "a\n")
    repo.commit("base")
    repo.git("switch", "-q", "-c", "side")
    repo.write("b.txt", "b\n")
    repo.commit("side")
    repo.git("switch", "-q", "main")
    backend = GitBackend(repo.path)
    assert backend.merge("side", no_ff=True) == []
    assert backend.log()[0].is_merge
    assert not backend.merge_in_progress()


def test_conflicting_merge_then_resolve_and_commit(diverged):
    repo, backend = diverged
    assert backend.merge("feature") == ["f.txt"]
    assert backend.merge_in_progress()
    assert "Merge branch 'feature'" in backend.merge_message()

    status = {s.path: s for s in backend.status()}
    assert status["f.txt"].is_conflicted and not status["f.txt"].is_staged
    assert status["g.txt"].is_staged

    base, ours, theirs = backend.conflict_versions("f.txt")
    assert (base, ours, theirs) == (b"one\ntwo\nthree\n", b"one\nTWO-main\nthree\n", b"one\nTWO-feature\nthree\n")

    working = (repo.path / "f.txt").read_text()
    assert has_conflict_markers(working)
    assert parse_conflicts(working)[0].base == ["two\n"]  # diff3 style includes the ancestor
    backend.mark_resolved("f.txt", resolve_all(working, Choice.THEIRS).encode())
    assert backend.conflicted_paths() == []

    backend.commit(backend.merge_message())
    assert not backend.merge_in_progress()
    head = backend.log()[0]
    assert head.is_merge and (repo.path / "f.txt").read_text() == "one\nTWO-feature\nthree\n"


def test_merge_abort(diverged):
    repo, backend = diverged
    backend.merge("feature")
    backend.merge_abort()
    assert not backend.merge_in_progress()
    assert (repo.path / "f.txt").read_text() == "one\nTWO-main\nthree\n"


def test_resolve_by_deleting(diverged):
    repo, backend = diverged
    backend.merge("feature")
    backend.mark_resolved("f.txt", None)
    assert backend.conflicted_paths() == []
    assert not (repo.path / "f.txt").exists()


def test_merge_with_dirty_tree_raises(diverged):
    repo, backend = diverged
    repo.write("f.txt", "uncommitted\n")
    from gitops.core import GitError

    with pytest.raises(GitError):
        backend.merge("feature")


# ---------------------------------------------------------------- conflict markers

MERGE_STYLE = """keep
<<<<<<< HEAD
ours 1
ours 2
=======
theirs 1
>>>>>>> feature
middle
<<<<<<< HEAD
o
=======
t
>>>>>>> feature
end
"""

DIFF3_STYLE = """<<<<<<< ours
O
||||||| base
B
=======
T
>>>>>>> theirs
"""


def test_parse_merge_style():
    blocks = parse_conflicts(MERGE_STYLE)
    assert len(blocks) == 2
    assert blocks[0].ours == ["ours 1\n", "ours 2\n"] and blocks[0].theirs == ["theirs 1\n"]
    assert blocks[0].ours_label == "HEAD" and blocks[0].theirs_label == "feature"
    assert blocks[0].base is None


def test_parse_diff3_style():
    (block,) = parse_conflicts(DIFF3_STYLE)
    assert (block.ours, block.base, block.theirs) == (["O\n"], ["B\n"], ["T\n"])
    assert resolve(DIFF3_STYLE, {0: Choice.BASE}) == "B\n"


def test_resolve_individual_blocks():
    assert resolve(MERGE_STYLE, {0: Choice.THEIRS, 1: Choice.BOTH}) == "keep\ntheirs 1\nmiddle\no\nt\nend\n"
    partially = resolve(MERGE_STYLE, {1: Choice.OURS})
    assert len(parse_conflicts(partially)) == 1 and partially.endswith("middle\no\nend\n")


def test_resolve_keeps_crlf():
    text = "a\r\n<<<<<<< HEAD\r\nx\r\n=======\r\ny\r\n>>>>>>> b\r\nz\r\n"
    assert resolve_all(text, Choice.OURS) == "a\r\nx\r\nz\r\n"


def test_unterminated_block_is_ignored():
    assert parse_conflicts("<<<<<<< HEAD\nx\n=======\n") == []


def test_markers_need_exact_prefix():
    assert parse_conflicts("<<<<<<<< not a marker\n") == []


# ---------------------------------------------------------------- stash


def test_stash_save_list_apply_pop_drop(repo):
    repo.write("a.txt", "a\n")
    repo.commit("base")
    backend = GitBackend(repo.path)
    assert backend.stash_save("nothing") is False

    repo.write("a.txt", "changed\n")
    repo.write("new.txt", "untracked\n")
    assert backend.stash_save("wip one", include_untracked=True)
    assert backend.status() == []
    (stash,) = backend.stashes()
    assert stash.ref == "stash@{0}" and "wip one" in stash.message
    diff = backend.stash_diff(stash.ref)
    assert "+changed" in diff and "+untracked" in diff

    assert backend.stash_apply(stash.ref) == []
    assert len(backend.stashes()) == 1
    backend.stash_save("again", include_untracked=True)
    assert backend.stash_apply("stash@{0}", pop=True) == []
    assert len(backend.stashes()) == 1
    backend.stash_drop("stash@{0}")
    assert backend.stashes() == []


def test_stash_keeps_staged_state(repo):
    repo.write("a.txt", "a\n")
    repo.commit("base")
    backend = GitBackend(repo.path)
    repo.write("a.txt", "staged\n")
    backend.stage("a.txt")
    backend.stash_save()
    backend.stash_apply(pop=True)
    assert backend.status()[0].is_staged


def test_stash_pop_conflict_keeps_stash(repo):
    repo.write("a.txt", "a\n")
    repo.commit("base")
    backend = GitBackend(repo.path)
    repo.write("a.txt", "stashed\n")
    backend.stash_save()
    repo.write("a.txt", "committed\n")
    repo.commit("conflicting")
    assert backend.stash_apply(pop=True) == ["a.txt"]
    assert len(backend.stashes()) == 1


# ---------------------------------------------------------------- tags and compare


def test_tags(repo):
    repo.write("a.txt", "a\n")
    sha = repo.commit("base")
    repo.git("tag", "light")
    repo.git("tag", "-a", "v1.0", "-m", "Release one")
    tags = {t.name: t for t in GitBackend(repo.path).tags()}
    assert tags["light"].target_sha == sha and not tags["light"].annotated
    assert tags["v1.0"].target_sha == sha and tags["v1.0"].annotated
    assert tags["v1.0"].message == "Release one"


def test_unified_diff_variants():
    assert "identical" in unified_diff(b"x\n", b"x\n", "a", "b")
    text = unified_diff(b"one\ntwo\n", b"one\nTWO", "old", "new")
    assert "-two" in text and "+TWO" in text and "No newline" in text
    assert "--- /dev/null" in unified_diff(None, b"x\n", "a", "b")
    assert "Binary" in unified_diff(b"\0", b"\1", "a", "b")
