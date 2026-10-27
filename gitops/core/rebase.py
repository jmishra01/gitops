"""Interactive rebase without an editor.

The UI produces a list of steps (pick / reword / squash / fixup / drop, in the
new order). That list is turned into a git-rebase-todo which is handed to git
through GIT_SEQUENCE_EDITOR. New commit messages (rewords and combined squash
messages) are applied with `exec git commit --amend -F <file>` right after the
commit they belong to, so git never needs to open an editor, even when the
rebase stops for conflicts and is continued later.
"""

from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass

from gitops.core.git_backend import COMMIT_FORMAT, RS, GitBackend, parse_commit_header
from gitops.core.models import Commit

ACTIONS = ("pick", "reword", "squash", "fixup", "drop")


@dataclass
class RebaseStep:
    commit: Commit
    action: str = "pick"
    message: str | None = None  # new message for "reword"


def rebase_plan(backend: GitBackend, base: str | None) -> list[RebaseStep]:
    """Commits after `base` up to HEAD, oldest first. `base=None` means from the root commit."""
    rev = f"{base}..HEAD" if base else "HEAD"
    out = backend.run("log", "--reverse", "--no-merges", f"--format={COMMIT_FORMAT}", rev)
    return [RebaseStep(parse_commit_header(line)) for line in out.split("\n") if line.startswith(RS)]


def has_merges(backend: GitBackend, base: str | None) -> bool:
    rev = f"{base}..HEAD" if base else "HEAD"
    return bool(backend.run("rev-list", "--merges", "--max-count=1", rev).strip())


def build_todo(backend: GitBackend, steps: list[RebaseStep], message_dir) -> str:
    for step in steps:
        if step.action not in ACTIONS:
            raise ValueError(f"Unknown action {step.action!r}")
    first = next((s for s in steps if s.action != "drop"), None)
    if first is not None and first.action in ("squash", "fixup"):
        raise ValueError("The first commit can't be squashed or fixed up: there's nothing before it to combine with.")

    lines: list[str] = []
    group: list[RebaseStep] = []
    message_count = 0

    def full_message(commit: Commit) -> str:
        return backend.run("log", "-1", "--format=%B", commit.sha).strip()

    def close_group() -> None:
        nonlocal message_count
        if not group:
            return
        head, squashes = group[0], [s for s in group[1:] if s.action == "squash"]
        if head.action == "reword" or squashes:
            message = head.message if head.action == "reword" and head.message else full_message(head.commit)
            for step in squashes:
                message += "\n\n" + full_message(step.commit)
            message_count += 1
            path = message_dir / f"message-{message_count}.txt"
            path.write_text(message.strip() + "\n")
            lines.append(f"exec git commit --amend -q --allow-empty -F {shlex.quote(str(path))}")
        group.clear()

    for step in steps:
        if step.action == "drop":
            lines.append(f"drop {step.commit.sha}")
        elif step.action in ("squash", "fixup"):
            lines.append(f"fixup {step.commit.sha}")
            group.append(step)
        else:
            close_group()
            lines.append(f"pick {step.commit.sha}")
            group.append(step)
    close_group()
    return "\n".join(lines) + "\n"


def run_interactive_rebase(backend: GitBackend, base: str | None, steps: list[RebaseStep]) -> list[str]:
    """Rewrite history as described by `steps`. Returns conflicted paths if git stops."""
    work = backend.git_path("gitops-rebase")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    todo = work / "todo"
    todo.write_text(build_todo(backend, steps, work))
    env = {"GIT_SEQUENCE_EDITOR": f"cp {shlex.quote(str(todo))}"}
    args = ["rebase", "-i", "--empty=drop"]
    args += [base] if base else ["--root"]
    return backend._stoppable(*args, env=env)  # noqa: SLF001 - shared conflict handling
