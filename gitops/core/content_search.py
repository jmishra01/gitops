"""Search text across the repo's entire history ("pickaxe" search).

For every commit whose diff touches a line containing the text, report
whether the text was Added, Changed or Removed in that commit:

* matches only on `+` lines  -> ADDED
* matches only on `-` lines  -> REMOVED
* matches on both            -> CHANGED (edited in place, or moved)

`git log -G` selects the candidate commits; the classification is done here by
counting matches in the commit's own diff.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from gitops.core.git_backend import COMMIT_FORMAT, RS, GitBackend, GitError, parse_commit_header
from gitops.core.models import ChangeKind, Commit, SearchHit

# Characters that are special in POSIX extended regexes (what `git log -G` uses).
_ERE_SPECIAL = set(".[]()*+?{}|^$\\")


@dataclass
class SearchOptions:
    text: str
    regex: bool = False
    ignore_case: bool = False
    all_refs: bool = False
    paths: list[str] = field(default_factory=list)  # pathspecs, e.g. ["*.py"]
    author: str | None = None
    since: str | None = None  # anything `git log --since` accepts
    until: str | None = None
    max_commits: int | None = None

    def git_pattern(self) -> str:
        if self.regex:
            return self.text
        return "".join("\\" + ch if ch in _ERE_SPECIAL else ch for ch in self.text)

    def python_pattern(self) -> re.Pattern[str]:
        flags = re.IGNORECASE if self.ignore_case else 0
        return re.compile(self.text if self.regex else re.escape(self.text), flags)


@dataclass(frozen=True)
class GrepMatch:
    path: str
    line_number: int
    text: str


@dataclass
class SearchSummary:
    present_now: bool
    current_matches: list[GrepMatch]
    first_added: SearchHit | None
    last_removed: SearchHit | None
    commit_count: int


def iter_search(backend: GitBackend, opts: SearchOptions) -> Iterator[SearchHit]:
    """Yield hits newest-first as git produces them.

    Results stream, so a UI can show them immediately; closing the iterator
    stops the underlying git process.
    """
    if not opts.text or not backend.has_commits():
        return

    args = [
        "log",
        f"--format={COMMIT_FORMAT}",
        "-p",
        "--unified=0",
        "--no-ext-diff",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "-M",
        "-G",
        opts.git_pattern(),
    ]
    if opts.ignore_case:
        args.append("--regexp-ignore-case")
    if opts.author:
        args.append(f"--author={opts.author}")
    if opts.since:
        args.append(f"--since={opts.since}")
    if opts.until:
        args.append(f"--until={opts.until}")
    if opts.max_commits is not None:
        args.append(f"--max-count={opts.max_commits}")
    args.append("--all" if opts.all_refs else "HEAD")
    args.append("--")
    args.extend(opts.paths)

    yield from _parse_patch_stream(backend.stream(*args), opts.python_pattern())


def search(backend: GitBackend, opts: SearchOptions) -> list[SearchHit]:
    return list(iter_search(backend, opts))


def grep_at(backend: GitBackend, opts: SearchOptions, rev: str | None = None) -> list[GrepMatch]:
    """Where the text appears at `rev` (or in the working tree when rev is None)."""
    args = ["grep", "-n", "-I", "--null", "-E" if opts.regex else "-F"]
    if opts.ignore_case:
        args.append("-i")
    args += ["-e", opts.text]
    if rev:
        args.append(rev)
    args.append("--")
    args.extend(opts.paths)

    proc = backend.run_raw(*args)
    if proc.returncode == 1:  # no matches
        return []
    if proc.returncode != 0:
        raise GitError(args, proc.returncode, proc.stderr.decode("utf-8", errors="replace"))

    matches: list[GrepMatch] = []
    prefix = f"{rev}:" if rev else ""
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        # With --null: "<rev:>path\0lineno\0text"
        path, lineno, text = line.split("\0", 2)
        matches.append(GrepMatch(path.removeprefix(prefix), int(lineno), text))
    return matches


def summarize(backend: GitBackend, opts: SearchOptions, hits: list[SearchHit]) -> SearchSummary:
    """Summary line for the search panel. `hits` must be newest-first."""
    current = grep_at(backend, opts) if opts.text else []
    added = [h for h in hits if h.kind is ChangeKind.ADDED]
    removed = [h for h in hits if h.kind is ChangeKind.REMOVED]
    return SearchSummary(
        present_now=bool(current),
        current_matches=current,
        first_added=added[-1] if added else None,
        last_removed=removed[0] if removed else None,
        commit_count=len({h.commit.sha for h in hits}),
    )


# ---------------------------------------------------------------------- parsing


def _parse_patch_stream(lines: Iterator[str], pattern: re.Pattern[str]) -> Iterator[SearchHit]:
    commit: Commit | None = None
    path: str | None = None
    old_path: str | None = None
    in_header = False
    added: list[str] = []
    removed: list[str] = []

    def flush() -> SearchHit | None:
        if commit is None or (path is None and old_path is None):
            return None
        if not added and not removed:
            return None
        kind = ChangeKind.CHANGED
        if added and not removed:
            kind = ChangeKind.ADDED
        elif removed and not added:
            kind = ChangeKind.REMOVED
        return SearchHit(commit, path or old_path or "", kind, list(added), list(removed))

    for line in lines:
        if line.startswith(RS) or line.startswith("diff --git "):
            hit = flush()
            if hit:
                yield hit
            added.clear()
            removed.clear()
            path = old_path = None
            if line.startswith(RS):
                commit = parse_commit_header(line)
                in_header = False
            else:
                in_header = True
            continue

        if in_header:
            if line.startswith("--- "):
                old_path = _strip_prefix(line[4:], "a/")
            elif line.startswith("+++ "):
                path = _strip_prefix(line[4:], "b/")
            elif line.startswith("@@"):
                in_header = False
            continue

        if line[:1] in ("+", "-"):
            content = line[1:]
            if pattern.search(content):
                (added if line[0] == "+" else removed).append(content)

    hit = flush()
    if hit:
        yield hit


def _strip_prefix(raw: str, prefix: str) -> str | None:
    raw = raw.rstrip("\t")
    if raw == "/dev/null":
        return None
    return raw.removeprefix(prefix)
