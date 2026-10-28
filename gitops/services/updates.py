"""Check GitHub for a newer GitOps release.

The only request is an anonymous GET of the latest release's metadata; no
data about the user or their repositories is sent.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from gitops import __version__

LATEST_RELEASE_API = "https://api.github.com/repos/jmishra01/gitops/releases/latest"
RELEASES_PAGE = "https://github.com/jmishra01/gitops/releases"
CHECK_INTERVAL = 24 * 3600  # seconds between automatic checks

_VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)$")


@dataclass(frozen=True)
class Release:
    version: str
    url: str


def parse_version(text: str) -> tuple[int, ...] | None:
    """`v0.3.2` or `0.3.2` -> (0, 3, 2); None for anything else, pre-releases included."""
    match = _VERSION.match(text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(candidate: str, current: str = __version__) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def latest_release(timeout: float = 10) -> Release | None:
    """The latest published release, or None if the project has none yet."""
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"GitOps/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # GitHub's answer when nothing has been released
            return None
        raise
    tag = str(data.get("tag_name", ""))
    return Release(tag.removeprefix("v"), str(data.get("html_url") or RELEASES_PAGE))


def newer_release(current: str = __version__, timeout: float = 10) -> Release | None:
    release = latest_release(timeout)
    return release if release and is_newer(release.version, current) else None
