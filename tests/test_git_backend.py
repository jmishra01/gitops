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


def test_commit_files_and_diff(repo):
    repo.write("keep.txt", "a\nb\n")
    repo.write("old.txt", "x\n" * 20)
    repo.commit("base")
    repo.write("keep.txt", "a\nB\nc\n")
    repo.git("mv", "old.txt", "renamed.txt")
    repo.write("new.bin", "\0\1\2")
    repo.write("gone.txt", "")
    repo.commit("changes")
    backend = GitBackend(repo.path)
    head = backend.log()[0]

    files = {f.path: f for f in backend.commit_files(head)}
    assert (files["keep.txt"].status, files["keep.txt"].added, files["keep.txt"].removed) == ("M", 2, 1)
    assert files["renamed.txt"].status == "R" and files["renamed.txt"].old_path == "old.txt"
    assert files["new.bin"].status == "A" and files["new.bin"].added is None  # binary
    assert "+B" in backend.commit_file_diff(head, files["keep.txt"])


def test_root_and_merge_commits_compare_with_first_parent(repo):
    repo.write("a.txt", "a\n")
    repo.commit("root")
    backend = GitBackend(repo.path)
    root = backend.log()[0]
    assert [f.path for f in backend.commit_files(root)] == ["a.txt"]

    repo.git("switch", "-q", "-c", "side")
    repo.write("side.txt", "s\n")
    repo.commit("side work")
    repo.git("switch", "-q", "main")
    repo.write("main.txt", "m\n")
    repo.commit("main work")
    repo.git("merge", "-q", "--no-edit", "side")
    merge = backend.log()[0]
    assert merge.is_merge
    assert [f.path for f in backend.commit_files(merge)] == ["side.txt"]  # what the merge brought in


def test_commit_message_and_detached_checkout(repo):
    repo.write("a.txt", "a\n")
    first = repo.commit("Subject line\n\nBody text here.")
    repo.write("a.txt", "b\n")
    repo.commit("second")
    backend = GitBackend(repo.path)
    assert backend.commit_message(first) == "Subject line\n\nBody text here."
    backend.checkout_detached(first)
    assert backend.current_branch() is None
    assert backend.run("rev-parse", "HEAD").strip() == first
