import pytest

from gitops.core import GitBackend
from gitops.core.encoding import decode_lossless, display_text, encode_lossless, pick_encoding
from gitops.core.patch import build_partial_patch, parse_file_patch

LATIN1 = b"caf\xe9 = 1\nline2\nline3\nna\xefve = 4\nline5\n"


def test_lossless_round_trip_of_any_bytes():
    data = bytes(range(256)) + "日本\n".encode()
    assert encode_lossless(decode_lossless(data)) == data


def test_display_text_shows_legacy_lines_readably():
    text = decode_lossless(b"caf\xe9\nplain\n\xe6\x97\xa5\xe6\x9c\xac\n")
    shown = display_text(text)
    assert shown == "café\nplain\n日本\n"
    assert shown.count("\n") == text.count("\n")  # line numbers still line up
    shown.encode("utf-8")  # no lone surrogates left for Qt to choke on


def test_pick_encoding():
    assert pick_encoding(b"plain", "日本".encode(), None) == "utf-8"
    assert pick_encoding(b"plain", b"caf\xe9") == "latin-1"
    assert pick_encoding() == "utf-8"


@pytest.fixture
def latin1_repo(repo):
    (repo.path / "l.txt").write_bytes(LATIN1)
    repo.commit("base")
    return repo, GitBackend(repo.path)


def index_bytes(backend, path="l.txt"):
    return backend.run_bytes("show", f":{path}")


def test_stage_hunk_in_latin1_file(latin1_repo):
    repo, backend = latin1_repo
    changed = LATIN1.replace(b"line2", b"LINE2")
    (repo.path / "l.txt").write_bytes(changed)
    patch = parse_file_patch(backend.file_diff("l.txt", staged=False))
    backend.apply_to_index(build_partial_patch(patch, patch.hunks[0].change_indices))
    assert index_bytes(backend) == changed


def test_stage_one_line_next_to_latin1_lines(latin1_repo):
    repo, backend = latin1_repo
    # Edit two lines, one of which contains a Latin-1 byte; stage only the plain one.
    changed = LATIN1.replace(b"line2", b"LINE2").replace(b"na\xefve = 4", b"na\xefve = 40")
    (repo.path / "l.txt").write_bytes(changed)
    patch = parse_file_patch(backend.file_diff("l.txt", staged=False))
    pick = {i for i, line in patch.hunks[0].lines if line in ("-line2", "+LINE2")}
    backend.apply_to_index(build_partial_patch(patch, pick))
    assert index_bytes(backend) == LATIN1.replace(b"line2", b"LINE2")


def test_stage_and_unstage_a_latin1_line(latin1_repo):
    repo, backend = latin1_repo
    changed = LATIN1.replace(b"caf\xe9 = 1", b"caf\xe9 = 2")
    (repo.path / "l.txt").write_bytes(changed)
    patch = parse_file_patch(backend.file_diff("l.txt", staged=False))
    backend.apply_to_index(build_partial_patch(patch, patch.hunks[0].change_indices))
    assert index_bytes(backend) == changed

    staged = parse_file_patch(backend.file_diff("l.txt", staged=True))
    backend.apply_to_index(build_partial_patch(staged, staged.hunks[0].change_indices, reverse=True), reverse=True)
    assert index_bytes(backend) == LATIN1
