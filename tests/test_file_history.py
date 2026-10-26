from gitops.core import ChangeKind, GitBackend
from gitops.core.file_history import file_history


def build_history(repo):
    repo.write("login.py", "a\nb\n")
    created = repo.commit("create login")
    repo.write("other.py", "unrelated\n")
    repo.commit("unrelated change")
    repo.write("login.py", "a\nc\nd\n")
    modified = repo.commit("edit login")
    repo.git("mv", "login.py", "auth.py")
    renamed = repo.commit("rename to auth")
    repo.write("auth.py", "a\nc\nd\ne\n")
    modified_after = repo.commit("edit auth")
    return created, modified, renamed, modified_after


def test_follows_renames(repo):
    created, modified, renamed, modified_after = build_history(repo)
    entries = file_history(GitBackend(repo.path), "auth.py")

    assert [e.commit.sha for e in entries] == [modified_after, renamed, modified, created]
    assert [e.kind for e in entries] == [
        ChangeKind.MODIFIED,
        ChangeKind.RENAMED,
        ChangeKind.MODIFIED,
        ChangeKind.CREATED,
    ]
    rename = entries[1]
    assert (rename.old_path, rename.path) == ("login.py", "auth.py")
    assert entries[3].path == "login.py"


def test_line_counts(repo):
    build_history(repo)
    entries = file_history(GitBackend(repo.path), "auth.py")
    assert (entries[0].added, entries[0].removed) == (1, 0)
    assert (entries[2].added, entries[2].removed) == (2, 1)
    assert (entries[3].added, entries[3].removed) == (2, 0)


def test_without_follow_stops_at_rename(repo):
    _, _, renamed, modified_after = build_history(repo)
    entries = file_history(GitBackend(repo.path), "auth.py", follow=False)
    assert [e.commit.sha for e in entries] == [modified_after, renamed]


def test_deleted_file(repo):
    repo.write("gone.txt", "x\n")
    repo.commit("add")
    (repo.path / "gone.txt").unlink()
    repo.commit("delete")
    entries = file_history(GitBackend(repo.path), "gone.txt")
    assert [e.kind for e in entries] == [ChangeKind.DELETED, ChangeKind.CREATED]
