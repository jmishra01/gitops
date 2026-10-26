"""Lane assignment for drawing the commit graph.

Commits arrive newest-first in an order where no parent precedes its
children (git log --date-order). Each lane holds the sha it is waiting to
reach; a commit lands in the first lane waiting for it, then hands that lane
to its first parent and opens (or joins) lanes for any other parents.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from gitops.core.models import Commit


@dataclass(frozen=True)
class GraphRow:
    column: int  # lane of this commit's node
    passthrough: tuple[int, ...]  # lanes drawn straight through the row
    incoming: tuple[int, ...]  # lanes from the row's top edge that end at the node
    outgoing: tuple[int, ...]  # lanes from the node to the row's bottom edge
    width: int  # lanes the row spans


def layout(commits: Sequence[Commit]) -> list[GraphRow]:
    lanes: list[str | None] = []
    rows: list[GraphRow] = []

    def free_slot() -> int:
        for i, sha in enumerate(lanes):
            if sha is None:
                return i
        lanes.append(None)
        return len(lanes) - 1

    for commit in commits:
        width_before = len(lanes)
        incoming = [i for i, sha in enumerate(lanes) if sha == commit.sha]
        for i in incoming:
            lanes[i] = None
        column = incoming[0] if incoming else free_slot()
        passthrough = [i for i, sha in enumerate(lanes) if sha is not None]

        outgoing: list[int] = []
        for n, parent in enumerate(commit.parents):
            existing = next((i for i, sha in enumerate(lanes) if sha == parent), None)
            if n == 0:
                # The first parent keeps this lane so mainlines stay straight; if
                # another lane also heads to it, the two converge at the parent.
                lanes[column] = parent
                outgoing.append(column)
            elif existing is not None:
                outgoing.append(existing)  # join a lane already heading to this parent
            else:
                slot = free_slot()
                lanes[slot] = parent
                outgoing.append(slot)

        width = max(width_before, len(lanes), column + 1)
        while lanes and lanes[-1] is None:
            lanes.pop()
        rows.append(GraphRow(column, tuple(passthrough), tuple(incoming), tuple(outgoing), width))
    return rows
