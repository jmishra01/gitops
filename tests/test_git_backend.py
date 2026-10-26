from gitops.core import GitBackend


def test_empty_repo(repo):
    backend = GitBackend(repo.path)
    assert not backend.has_commits()
    assert backend.log() == []
    assert backend.branches() == []


def test_branches_and_current(repo):
    repo.write("a.txt", "one\n")
    repo.commit("first")
    repo.git("branch", "feature/login")
    backend = GitBackend(repo.path)

    names = {b.name: b for b in backend.branches()}
    assert set(names) == {"main", "feature/login"}
    assert names["main"].is_current
    assert backend.current_branch() == "main"


def test_status_staged_unstaged_untracked_and_rename(repo):
    repo.write("a.txt", "one\n")
    repo.write("old.txt", "keep\n")
    repo.commit("first")
    repo.write("a.txt", "two\n")
    repo.write("new file.txt", "x\n")
    repo.git("mv", "old.txt", "renamed.txt")

    by_path = {s.path: s for s in GitBackend(repo.path).status()}
    assert by_path["a.txt"].is_unstaged and not by_path["a.txt"].is_staged
    assert by_path["new file.txt"].index == "?"
    assert by_path["renamed.txt"].index == "R"
    assert by_path["renamed.txt"].orig_path == "old.txt"


def test_log_and_show_file(repo):
    repo.write("a.txt", "v1\n")
    first = repo.commit("first", author="Alice")
    repo.write("a.txt", "v2\n")
    repo.commit("second", author="Bob")
    backend = GitBackend(repo.path)

    log = backend.log()
    assert [c.subject for c in log] == ["second", "first"]
    assert log[0].author == "Bob"
    assert log[0].parents == (first,)
    assert backend.show_file(first, "a.txt") == b"v1\n"
