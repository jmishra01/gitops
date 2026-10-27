import pytest

from gitops.core import GitBackend
from gitops.core.patch import build_partial_patch, parse_file_patch

ORIGINAL = [f"line {n}" for n in range(1, 21)]


def text(lines, trailing_newline=True):
    return "\n".join(lines) + ("\n" if trailing_newline else "")


@pytest.fixture
def backend(repo):
    repo.write("f.txt", text(ORIGINAL))
    repo.commit("base")
    return GitBackend(repo.path)


def index_content(backend, path="f.txt"):
    return backend.run("show", f":{path}")


def changed(**edits):
    lines = list(ORIGINAL)
    for n, value in edits.items():
        lines[int(n.removeprefix("l")) - 1] = value
    return lines


def test_stage_one_hunk_of_two(repo, backend):
    repo.write("f.txt", text(changed(l2="TWO", l15="FIFTEEN")))
    patch = parse_file_patch(backend.file_diff("f.txt", staged=False))
    assert len(patch.hunks) == 2

    backend.apply_to_index(build_partial_patch(patch, patch.hunks[1].change_indices))
    assert index_content(backend) == text(changed(l15="FIFTEEN"))


def test_stage_single_line_pair_inside_hunk(repo, backend):
    repo.write("f.txt", text(changed(l5="FIVE", l6="SIX", l7="SEVEN")))
    patch = parse_file_patch(backend.file_diff("f.txt", staged=False))
    lines = dict(patch.hunks[0].lines)
    pick = {i for i, line in lines.items() if line in ("-line 6", "+SIX")}

    backend.apply_to_index(build_partial_patch(patch, pick))
    assert index_content(backend) == text(changed(l6="SIX"))


def test_stage_only_the_addition(repo, backend):
    repo.write("f.txt", text(changed(l5="FIVE")))
    patch = parse_file_patch(backend.file_diff("f.txt", staged=False))
    plus = {i for i, line in patch.hunks[0].lines if line == "+FIVE"}

    backend.apply_to_index(build_partial_patch(patch, plus))
    expected = list(ORIGINAL)
    expected.insert(5, "FIVE")  # added after the still-present "line 5"
    assert index_content(backend) == text(expected)


def test_unstage_one_line_from_staged(repo, backend):
    repo.write("f.txt", text(changed(l5="FIVE", l6="SIX", l7="SEVEN")))
    backend.stage("f.txt")
    patch = parse_file_patch(backend.file_diff("f.txt", staged=True))
    pick = {i for i, line in patch.hunks[0].lines if line in ("-line 7", "+SEVEN")}

    backend.apply_to_index(build_partial_patch(patch, pick, reverse=True), reverse=True)
    assert index_content(backend) == text(changed(l5="FIVE", l6="SIX"))


def test_offsets_when_earlier_hunk_skipped(repo, backend):
    lines = list(ORIGINAL)
    lines[1:2] = ["TWO-a", "TWO-b", "TWO-c"]  # first hunk grows the file by 2
    lines[16] = "SEVENTEEN"  # original line 15
    repo.write("f.txt", text(lines))
    patch = parse_file_patch(backend.file_diff("f.txt", staged=False))
    assert len(patch.hunks) == 2

    backend.apply_to_index(build_partial_patch(patch, patch.hunks[1].change_indices))
    assert index_content(backend) == text(changed(l15="SEVENTEEN"))

    # And the reverse direction: stage everything, then unstage only hunk 2.
    backend.stage("f.txt")
    staged = parse_file_patch(backend.file_diff("f.txt", staged=True))
    backend.apply_to_index(build_partial_patch(staged, staged.hunks[1].change_indices, reverse=True), reverse=True)
    expected = list(ORIGINAL)
    expected[1:2] = ["TWO-a", "TWO-b", "TWO-c"]
    assert index_content(backend) == text(expected)


def test_no_newline_at_end_of_file(repo, backend):
    repo.write("f.txt", text(ORIGINAL[:-1] + ["LAST"], trailing_newline=False))
    patch = parse_file_patch(backend.file_diff("f.txt", staged=False))
    backend.apply_to_index(build_partial_patch(patch, patch.hunks[0].change_indices))
    assert index_content(backend) == text(ORIGINAL[:-1] + ["LAST"], trailing_newline=False)


def test_selection_helpers(repo, backend):
    repo.write("f.txt", text(changed(l2="TWO", l15="FIFTEEN")))
    patch = parse_file_patch(backend.file_diff("f.txt", staged=False))
    h0, h1 = patch.hunks
    assert patch.hunk_at(h1.header_index + 1) is h1
    assert patch.changes_in(h0.header_index, h0.header_index) == set(h0.change_indices)
    assert build_partial_patch(patch, []) is None


def test_binary_cannot_stage_lines(repo, backend):
    (repo.path / "img.bin").write_bytes(b"\x00\x01\x02")
    repo.commit("bin")
    (repo.path / "img.bin").write_bytes(b"\x00\x01\x03")
    assert not parse_file_patch(backend.file_diff("img.bin", staged=False)).can_stage_lines


def test_file_level_stage_unstage_discard(repo, backend):
    repo.write("f.txt", text(changed(l1="ONE")))
    repo.write("new.txt", "hello\n")
    backend.stage_all()
    assert {s.path for s in backend.status() if s.is_staged} == {"f.txt", "new.txt"}

    backend.unstage("new.txt")
    assert {s.path for s in backend.status() if s.is_staged} == {"f.txt"}

    backend.unstage_all()
    backend.discard("f.txt")
    backend.delete_untracked("new.txt")
    assert backend.status() == []


def test_commit_and_amend(repo, backend):
    repo.write("f.txt", text(changed(l1="ONE")))
    backend.stage("f.txt")
    sha = backend.commit("Change line one\n\nBody text.")
    assert backend.last_commit_message() == "Change line one\n\nBody text."

    amended = backend.commit("Better subject", amend=True)
    assert amended != sha
    assert [c.subject for c in backend.log()] == ["Better subject", "base"]


def test_stage_and_unstage_in_repo_without_commits(repo):
    backend = GitBackend(repo.path)
    repo.write("a.txt", "a\n")
    backend.stage("a.txt")
    assert backend.status()[0].index == "A"
    backend.unstage("a.txt")
    assert backend.status()[0].index == "?"


def test_change_counts(repo, backend):
    repo.write("f.txt", text(changed(l1="ONE", l2="TWO")))
    repo.write("g.txt", "new\nfile\n")
    backend.stage("g.txt")
    unstaged, staged = backend.change_counts()
    assert unstaged == {"f.txt": (2, 2)}
    assert staged == {"g.txt": (2, 0)}


def test_change_counts_before_first_commit(tmp_path):
    from gitops.core import GitBackend as GB

    b = GB.init(tmp_path / "empty")
    (tmp_path / "empty" / "a.txt").write_text("x\ny\n")
    b.stage("a.txt")
    assert b.change_counts() == ({}, {"a.txt": (2, 0)})


def test_discard_selected_lines_only(repo, backend):
    repo.write("f.txt", text(changed(l5="FIVE", l6="SIX")))
    patch = parse_file_patch(backend.file_diff("f.txt", staged=False))
    pick = {i for i, line in patch.hunks[0].lines if line in ("-line 6", "+SIX")}
    backend.discard_from_worktree(build_partial_patch(patch, pick, reverse=True))
    assert (repo.path / "f.txt").read_text() == text(changed(l5="FIVE"))
    assert index_content(backend) == text(ORIGINAL)  # the index is untouched
