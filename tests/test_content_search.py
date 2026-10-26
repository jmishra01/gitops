import pytest

from gitops.core import ChangeKind, GitBackend
from gitops.core.content_search import SearchOptions, grep_at, iter_search, search, summarize


@pytest.fixture
def history(repo):
    shas = {}
    repo.write("login.py", "def basic_auth(user):\n    return basic_auth_impl(u)\n")
    shas["added"] = repo.commit("Initial login module")
    repo.write("api.py", "from utils import basic_auth\n")
    shas["added_api"] = repo.commit("Use basic auth in api", author="Bob")
    repo.write("login.py", "def basic_auth(user):\n    return basic_auth_impl(user)\n")
    shas["changed"] = repo.commit("Fix argument name")
    repo.write("README.md", "docs\n")
    repo.commit("Unrelated docs")
    repo.write("login.py", "def login(user):\n    return oauth(user)\n")
    shas["removed"] = repo.commit("Switch to OAuth")
    return repo, shas


def kinds(hits):
    return [(h.commit.sha, h.path, h.kind) for h in hits]


def test_classifies_added_changed_removed(history):
    repo, s = history
    hits = search(GitBackend(repo.path), SearchOptions("basic_auth"))
    assert kinds(hits) == [
        (s["removed"], "login.py", ChangeKind.REMOVED),
        (s["changed"], "login.py", ChangeKind.CHANGED),
        (s["added_api"], "api.py", ChangeKind.ADDED),
        (s["added"], "login.py", ChangeKind.ADDED),
    ]
    assert hits[1].removed_lines == ["    return basic_auth_impl(u)"]
    assert hits[1].added_lines == ["    return basic_auth_impl(user)"]


def test_unrelated_commits_excluded(history):
    repo, _ = history
    subjects = {h.commit.subject for h in search(GitBackend(repo.path), SearchOptions("basic_auth"))}
    assert "Unrelated docs" not in subjects


def test_fixed_string_escapes_regex_chars(history):
    repo, s = history
    hits = search(GitBackend(repo.path), SearchOptions("impl(u)"))
    assert kinds(hits) == [(s["changed"], "login.py", ChangeKind.REMOVED), (s["added"], "login.py", ChangeKind.ADDED)]


def test_regex_and_ignore_case(history):
    repo, s = history
    backend = GitBackend(repo.path)
    assert search(backend, SearchOptions("BASIC_AUTH")) == []
    hits = search(backend, SearchOptions("^def BASIC_", regex=True, ignore_case=True))
    assert kinds(hits) == [(s["removed"], "login.py", ChangeKind.REMOVED), (s["added"], "login.py", ChangeKind.ADDED)]


def test_path_and_author_filters(history):
    repo, s = history
    backend = GitBackend(repo.path)
    assert kinds(search(backend, SearchOptions("basic_auth", paths=["api.py"]))) == [
        (s["added_api"], "api.py", ChangeKind.ADDED)
    ]
    assert {h.commit.author for h in search(backend, SearchOptions("basic_auth", author="Bob"))} == {"Bob"}


def test_all_refs_finds_text_on_other_branches(history):
    repo, _ = history
    repo.git("checkout", "-q", "-b", "experiment")
    repo.write("exp.py", "secret_flag = True\n")
    repo.commit("experiment")
    repo.git("checkout", "-q", "main")
    backend = GitBackend(repo.path)
    assert search(backend, SearchOptions("secret_flag")) == []
    assert len(search(backend, SearchOptions("secret_flag", all_refs=True))) == 1


def test_grep_at_revision_and_summary(history):
    repo, s = history
    backend = GitBackend(repo.path)
    opts = SearchOptions("basic_auth")

    at_changed = grep_at(backend, opts, s["changed"])
    assert {(m.path, m.line_number) for m in at_changed} == {("api.py", 1), ("login.py", 1), ("login.py", 2)}

    summary = summarize(backend, opts, search(backend, opts))
    assert summary.present_now  # api.py still imports it
    assert [m.path for m in summary.current_matches] == ["api.py"]
    assert summary.first_added.commit.sha == s["added"]
    assert summary.last_removed.commit.sha == s["removed"]
    assert summary.commit_count == 4


def test_closing_stream_early_stops(history):
    repo, _ = history
    it = iter_search(GitBackend(repo.path), SearchOptions("basic_auth"))
    first = next(it)
    it.close()
    assert first.kind is ChangeKind.REMOVED


def test_empty_query_and_empty_repo(repo):
    backend = GitBackend(repo.path)
    assert search(backend, SearchOptions("anything")) == []
    assert search(backend, SearchOptions("")) == []
