import subprocess

import pytest

from gitops.core import GitBackend, GitError


@pytest.fixture
def backend(repo):
    repo.write("a.txt", "a\n")
    repo.commit("first")
    return GitBackend(repo.path)


def test_create_checkout_rename_delete(backend):
    backend.create_branch("feature/x")
    assert backend.current_branch() == "feature/x"

    backend.checkout("main")
    backend.rename_branch("feature/x", "feature/y")
    assert {b.name for b in backend.branches()} == {"main", "feature/y"}

    backend.delete_branch("feature/y")
    assert {b.name for b in backend.branches()} == {"main"}


def test_delete_unmerged_needs_force(repo, backend):
    backend.create_branch("wip")
    repo.write("b.txt", "b\n")
    repo.commit("wip work")
    backend.checkout("main")
    with pytest.raises(GitError):
        backend.delete_branch("wip")
    backend.delete_branch("wip", force=True)


def test_branch_name_validation(backend):
    assert backend.is_valid_branch_name("feature/login")
    assert not backend.is_valid_branch_name("bad..name")
    assert not backend.is_valid_branch_name("")


@pytest.fixture
def remote(tmp_path, backend):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    backend.run("remote", "add", "origin", str(bare))
    return bare


def test_push_sets_upstream_then_ahead_behind(repo, backend, remote):
    assert backend.ahead_behind() is None
    progress = list(backend.push())
    assert progress  # git reported something
    assert backend.ahead_behind() == (0, 0)

    repo.write("a.txt", "changed\n")
    repo.commit("local change")
    assert backend.ahead_behind() == (1, 0)


def test_clone_fetch_pull_and_track_remote_branch(tmp_path, repo, backend, remote):
    list(backend.push())
    backend.create_branch("feature")
    list(backend.push())
    backend.checkout("main")

    clone_dir = tmp_path / "clone"
    list(GitBackend.clone(str(remote), clone_dir))
    other = GitBackend(clone_dir)
    assert other.current_branch() == "main"

    repo.write("a.txt", "upstream change\n")
    repo.commit("upstream change")
    list(backend.push())

    list(other.fetch())
    assert other.ahead_behind() == (0, 1)
    list(other.pull())
    assert other.log()[0].subject == "upstream change"

    assert other.checkout_remote("origin/feature") == "feature"
    assert other.current_branch() == "feature"


def test_checkout_remote_branch_of_remote_with_slash(tmp_path, repo, backend, remote):
    backend.create_branch("feature")
    list(backend.push())
    clone_dir = tmp_path / "clone"
    list(GitBackend.clone(str(remote), clone_dir))
    other = GitBackend(clone_dir)
    other.run("remote", "add", "team/x", str(remote))
    list(other.fetch("team/x"))

    assert other.checkout_remote("team/x/feature") == "feature"
    assert other.current_branch() == "feature"


def test_push_without_remote_errors(backend):
    with pytest.raises(GitError, match="no remote"):
        list(backend.push())


def test_progress_failure_raises(tmp_path, backend):
    with pytest.raises(GitError):
        list(GitBackend.clone(str(tmp_path / "does-not-exist"), tmp_path / "x"))


def test_init(tmp_path):
    created = GitBackend.init(tmp_path / "fresh")
    assert not created.has_commits()
    assert GitBackend.is_repo(tmp_path / "fresh")


def test_log_includes_refs(backend):
    backend.run("tag", "v1")
    refs = backend.log()[0].refs
    assert "HEAD -> main" in refs
    assert "tag: v1" in refs


def test_branch_details_track_and_tip(repo, backend, remote):
    list(backend.push())
    repo.write("a.txt", "ahead\n")
    repo.commit("local work", author="Lena")
    (main,) = [b for b in backend.branches() if b.name == "main"]
    assert (main.ahead, main.behind, main.upstream, main.upstream_gone) == (1, 0, "origin/main", False)
    assert (main.subject, main.author) == ("local work", "Lena") and main.timestamp > 0

    backend.create_branch("feature")
    list(backend.push())
    backend.run("push", "-q", "origin", "--delete", "feature")
    backend.run("fetch", "-q", "--prune")
    (feature,) = [b for b in backend.branches() if b.name == "feature"]
    assert feature.upstream_gone
