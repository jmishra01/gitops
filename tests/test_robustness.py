import os
import stat
import subprocess
import sys

import pytest

from gitops.core import GitBackend, GitError
from gitops.core.git_backend import _english_env, _messages_need_english

# ---------------------------------------------------------------- locale


@pytest.mark.parametrize(
    "environ, needed",
    [
        ({}, False),
        ({"LANG": "C.UTF-8"}, False),
        ({"LC_CTYPE": "de_DE.UTF-8"}, False),  # only character handling, messages stay English
        ({"LANG": "en_US.UTF-8"}, False),
        ({"LANG": "de_DE.UTF-8"}, True),
        ({"LANG": "en_US.UTF-8", "LANGUAGE": "fr:en"}, True),
        ({"LANG": "C", "LANGUAGE": "fr"}, False),  # gettext ignores LANGUAGE under C
        ({"LC_ALL": "ja_JP.UTF-8", "LANG": "en_US.UTF-8"}, True),
    ],
)
def test_messages_need_english(environ, needed):
    assert _messages_need_english(environ) is needed


def test_english_env_keeps_character_handling():
    env = _english_env({"LC_ALL": "de_DE.UTF-8", "LANGUAGE": "de", "PATH": "/bin"})
    assert env["LC_MESSAGES"] == "C"
    assert env["LC_CTYPE"] == "de_DE.UTF-8"  # LC_ALL is split, not discarded
    assert "LC_ALL" not in env and "LANGUAGE" not in env and env["PATH"] == "/bin"


# ---------------------------------------------------------------- no terminal prompts


@pytest.fixture
def backend(repo):
    repo.write("a.txt", "a\n")
    repo.commit("base")
    return GitBackend(repo.path)


def test_git_gets_no_stdin(backend):
    # Reading stdin returns end-of-file at once instead of waiting on the terminal or pytest.
    blob = backend.run("hash-object", "--stdin").strip()
    assert blob == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"  # the empty blob


@pytest.mark.skipif(sys.platform == "win32", reason="sessions and /dev/tty are POSIX concepts")
def test_hooks_cannot_reach_a_terminal(repo, backend):
    # A hook stands in for ssh/gpg: anything git starts must find no controlling terminal to prompt on.
    report = repo.path / "tty-report"
    hook = repo.path / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        f'#!/bin/sh\nif ( : < /dev/tty ) 2>/dev/null; then echo has-tty; else echo no-tty; fi > "{report}"\n'
    )
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    repo.write("a.txt", "changed\n")
    backend.stage("a.txt")
    backend.commit("hook check")
    assert report.read_text().strip() == "no-tty"


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX; on Windows Cancel stops git itself")
def test_cancel_kills_the_whole_process_group(tmp_path, backend, monkeypatch):
    # A custom git "command" that spawns a long-lived child, like git does with ssh.
    marker = tmp_path / "child.pid"
    script = tmp_path / "git-slowchild"
    script.write_text(f'#!/bin/sh\nsleep 30 &\necho $! > "{marker}"\necho started\nwait\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    stream = backend.stream("slowchild")
    assert next(stream) == "started"
    stream.close()  # what Cancel does
    child = int(marker.read_text())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        subprocess.run(["sleep", "0.1"])
    else:
        pytest.fail("the grandchild process survived cancellation")


# ---------------------------------------------------------------- huge files


def test_untracked_preview_reads_only_the_start(repo, backend):
    big = repo.path / "big.log"
    big.write_bytes(b"line of log output\n" * 200_000)  # ~3.8 MB
    preview = backend.untracked_preview("big.log")
    assert len(preview) < 1.2 * backend.PREVIEW_BYTES  # bounded (the "+" per line adds a little), not 3.8 MB
    assert "showing the first 512 KB of 3.6 MB" in preview
    assert preview.splitlines()[2] == "+line of log output"


def test_untracked_binary_preview_reports_size(repo, backend):
    (repo.path / "blob.bin").write_bytes(b"\0" * 1000)
    assert backend.untracked_preview("blob.bin") == "New binary file blob.bin (1,000 bytes)"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need extra privileges on Windows")
def test_untracked_symlink_preview_shows_the_link(repo, backend):
    (repo.path / "folder").mkdir()
    (repo.path / "folder" / "inside.txt").write_text("x\n")
    os.symlink("folder", repo.path / "to-dir")
    os.symlink("missing", repo.path / "dangling")
    os.symlink("a.txt", repo.path / "to-file")
    assert backend.untracked_preview("to-dir") == "New symlink to-dir\n\n+folder"
    assert backend.untracked_preview("dangling") == "New symlink dangling\n\n+missing"
    assert backend.untracked_preview("to-file") == "New symlink to-file\n\n+a.txt"  # not a.txt's content


# ---------------------------------------------------------------- opened on a subfolder


def test_backend_opened_on_a_subfolder_works_from_the_top_level(repo, backend):
    repo.write("sub/x.txt", "x\n")
    repo.commit("add sub")
    repo.write("a.txt", "changed\n")
    sub = GitBackend(repo.path / "sub")
    assert [s.path for s in sub.status()] == ["a.txt"]
    assert "+changed" in sub.file_diff("a.txt", staged=False)
    sub.stage("a.txt")
    assert "+changed" in sub.file_diff("a.txt", staged=True)
    assert len(sub.blame("sub/x.txt")) == 1


def test_stash_before_first_commit_explains_why(tmp_path):
    empty = GitBackend.init(tmp_path / "empty")
    (tmp_path / "empty" / "f").write_text("1")
    empty.stage("f")
    with pytest.raises(GitError, match="before the first commit"):
        empty.stash_save("x")


# ---------------------------------------------------------------- .gitignore bytes


def test_add_ignore_rule_keeps_latin1_and_crlf(repo, backend):
    original = b"# caf\xe9 rules\r\n*.tmp\r\n"
    (repo.path / ".gitignore").write_bytes(original)
    backend.add_ignore_rule("build/")
    assert (repo.path / ".gitignore").read_bytes() == original + b"build/\r\n"


def test_status_never_rewrites_the_index(repo, backend):
    """Background refreshes must not take index.lock (it would break the user's terminal git)."""
    index = repo.path / ".git" / "index"
    os.utime(repo.path / "a.txt")  # same content, new mtime: plain `git status` would refresh the index
    before = index.stat().st_mtime_ns
    backend.status()
    backend.change_counts()
    assert index.stat().st_mtime_ns == before
