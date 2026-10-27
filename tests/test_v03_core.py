import subprocess

import pytest

from gitops.core import GitBackend


@pytest.fixture
def backend(repo):
    repo.write("a.txt", "a\n")
    repo.commit("base")
    return GitBackend(repo.path)


def test_worktrees_add_list_remove(tmp_path, backend):
    (main,) = backend.worktrees()
    assert main.is_main and main.branch == "main"

    extra = tmp_path / "wt-feature"
    backend.add_worktree(str(extra), "feature", new_branch=True)
    trees = backend.worktrees()
    assert [t.branch for t in trees] == ["main", "feature"]
    assert not trees[1].is_main and (extra / "a.txt").exists()
    assert GitBackend(extra).current_branch() == "feature"

    backend.remove_worktree(str(extra))
    assert len(backend.worktrees()) == 1


def test_submodules_listed_with_state(tmp_path, repo, backend):
    lib = tmp_path / "lib"
    lib.mkdir()
    identity = ["-c", "user.name=A", "-c", "user.email=a@example.com"]
    for args in (["init", "-q", "-b", "main"], [*identity, "commit", "-q", "--allow-empty", "-m", "lib"]):
        subprocess.run(["git", "-C", str(lib), *args], check=True)
    repo.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "vendor/lib")
    repo.commit("add submodule")

    (sub,) = backend.submodules()
    assert sub.path == "vendor/lib" and sub.state == "ok" and len(sub.sha) == 40

    repo.git("submodule", "deinit", "-q", "-f", "vendor/lib")
    assert backend.submodules()[0].state == "not initialized"


@pytest.mark.parametrize("path", ["my lib", "x (y)"])
def test_submodule_path_with_spaces_and_parentheses(tmp_path, repo, backend, path):
    lib = tmp_path / "lib"
    lib.mkdir()
    identity = ["-c", "user.name=A", "-c", "user.email=a@example.com"]
    for args in (["init", "-q", "-b", "main"], [*identity, "commit", "-q", "--allow-empty", "-m", "lib"]):
        subprocess.run(["git", "-C", str(lib), *args], check=True)
    repo.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), path)
    repo.commit("add submodule")

    (sub,) = backend.submodules()
    assert sub.path == path and sub.describe == "heads/main"

    repo.git("submodule", "deinit", "-q", "-f", path)  # no describe text at all now
    (sub,) = backend.submodules()
    assert sub.path == path and sub.state == "not initialized"


def test_no_submodules(backend):
    assert backend.submodules() == []


def test_ignore_rules(repo, backend):
    assert backend.check_ignore("build/out.o") is None
    backend.add_ignore_rule("build/")
    backend.add_ignore_rule("*.log")
    backend.add_ignore_rule("*.log")  # no duplicates
    assert backend.read_gitignore() == "build/\n*.log\n"
    assert backend.check_ignore("build/out.o") == (".gitignore", 1, "build/")
    assert backend.check_ignore("debug.log")[2] == "*.log"

    backend.add_ignore_rule("!keep.log")
    assert backend.check_ignore("keep.log") is None

    backend.write_gitignore("no-trailing-newline")
    assert backend.read_gitignore() == "no-trailing-newline\n"


def test_summarize_repo(repo, backend):
    repo.write("a.txt", "changed\n")
    repo.write("new.txt", "x\n")
    summary = GitBackend.summarize_repo(str(repo.path))
    assert summary.name == "repo" and summary.branch == "main"
    assert summary.changed == 2 and summary.conflicts == 0
    assert summary.last_commit.subject == "base"
    assert summary.ahead_behind is None and summary.error is None


def test_summarize_missing_repo(tmp_path):
    summary = GitBackend.summarize_repo(str(tmp_path / "nope"))
    assert summary.error


def test_find_repos(tmp_path):
    for name in ("one", "group/two", "group/two/nested-ignored", ".hidden/three"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    for name in ("one", "group/two", ".hidden/three"):
        subprocess.run(["git", "init", "-q", str(tmp_path / name)], check=True)
    found = GitBackend.find_repos(str(tmp_path))
    assert found == [str(tmp_path / "group/two"), str(tmp_path / "one")]


def test_check_unsaved_ignore_rules():
    assert GitBackend.check_ignore_rules("build/\n*.log\n", "x/debug.log") == (2, "*.log")
    assert GitBackend.check_ignore_rules("*.log\n!keep.log\n", "keep.log") is None
    assert GitBackend.check_ignore_rules("", "anything") is None
