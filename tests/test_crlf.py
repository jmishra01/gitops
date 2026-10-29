"""Files with Windows line endings (CRLF), on every OS.

The bytes git stores keep their CRLF exactly; only what's shown drops the CR.
"""

from gitops.core import GitBackend
from gitops.core.content_search import SearchOptions, search
from gitops.core.encoding import display_text
from gitops.core.patch import build_partial_patch, parse_file_patch

BASE = b"one\r\ntwo\r\nthree\r\n"


def crlf_repo(repo):
    (repo.path / "f.txt").write_bytes(BASE)
    repo.commit("base")
    return GitBackend(repo.path)


def test_display_text_drops_the_cr_only():
    assert display_text("a\r\nb\r\nc") == "a\nb\nc"
    assert display_text("keep\rinside\r\n") == "keep\rinside\n"


def test_staging_one_line_keeps_crlf_bytes(repo):
    backend = crlf_repo(repo)
    (repo.path / "f.txt").write_bytes(b"one\r\nTWO\r\nthree\r\nfour\r\n")
    patch = parse_file_patch(backend.file_diff("f.txt", staged=False))
    lines = dict(patch.hunks[0].lines)
    assert "+four\r" in lines.values()  # the patch keeps the CR, so applying it is exact
    pick = {i for i, line in lines.items() if line == "+four\r"}

    backend.apply_to_index(build_partial_patch(patch, pick))
    assert backend.run_bytes("show", ":f.txt") == b"one\r\ntwo\r\nthree\r\nfour\r\n"


def test_blame_text_has_no_cr(repo):
    backend = crlf_repo(repo)
    assert [line.text for line in backend.blame("f.txt")] == ["one", "two", "three"]


def test_search_reports_lines_without_cr(repo):
    backend = crlf_repo(repo)
    (repo.path / "f.txt").write_bytes(b"one\r\ntwo-changed\r\nthree\r\n")
    repo.commit("change two")
    hits = search(backend, SearchOptions("two"))
    assert hits[0].removed_lines == ["two"] and hits[0].added_lines == ["two-changed"]
