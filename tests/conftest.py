from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


class RepoBuilder:
    """Builds throwaway repos with deterministic authors and timestamps."""

    def __init__(self, path: Path):
        self.path = path
        self._tick = 1_700_000_000
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.path), *args], check=True, capture_output=True, text=True).stdout

    def write(self, rel: str, content: str) -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self, message: str, author: str = "Alice") -> str:
        self._tick += 3600
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": author,
            "GIT_AUTHOR_EMAIL": f"{author.lower()}@example.com",
            "GIT_COMMITTER_NAME": author,
            "GIT_COMMITTER_EMAIL": f"{author.lower()}@example.com",
            "GIT_AUTHOR_DATE": f"{self._tick} +0000",
            "GIT_COMMITTER_DATE": f"{self._tick} +0000",
        }
        subprocess.run(["git", "-C", str(self.path), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(self.path), "commit", "-q", "-m", message], check=True, env=env)
        return self.git("rev-parse", "HEAD").strip()


@pytest.fixture(autouse=True)
def isolated_git_config(monkeypatch, tmp_path_factory):
    """Keep the developer's ~/.gitconfig (aliases, diff prefixes, signing...) out of tests."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def repo(tmp_path) -> RepoBuilder:
    return RepoBuilder(tmp_path / "repo")
