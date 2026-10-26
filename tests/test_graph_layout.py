from gitops.core.graph_layout import layout
from gitops.core.models import Commit


def c(sha, *parents):
    return Commit(sha, sha, "a", "a@x", 0, sha, tuple(parents))


def test_linear_history_stays_in_one_lane():
    rows = layout([c("c", "b"), c("b", "a"), c("a")])
    assert [r.column for r in rows] == [0, 0, 0]
    assert rows[0].incoming == () and rows[0].outgoing == (0,)
    assert rows[2].outgoing == ()  # root commit
    assert all(r.width == 1 for r in rows)


def test_branch_and_merge():
    #   m      merge of f into main
    #   |\
    #   | f
    #   b |
    #   |/
    #   a
    rows = layout([c("m", "b", "f"), c("f", "a"), c("b", "a"), c("a")])
    merge, feature, main, base = rows
    assert merge.column == 0 and merge.outgoing == (0, 1)
    assert feature.column == 1 and feature.passthrough == (0,)
    assert main.column == 0 and main.passthrough == (1,)
    # Both lanes converge on the base commit.
    assert base.column == 0 and set(base.incoming) == {0, 1}
    assert max(r.width for r in rows) == 2


def test_two_branch_tips_get_separate_lanes():
    rows = layout([c("x", "a"), c("y", "a"), c("a")])
    assert rows[0].column == 0
    assert rows[1].column == 1 and rows[1].passthrough == (0,)
    assert set(rows[2].incoming) == {0, 1}


def test_lane_is_reused_after_it_closes():
    rows = layout([c("m", "b", "f"), c("f", "b"), c("b", "a"), c("x", "a"), c("a")])
    # f's lane merges into b at row 2, so the later tip x can take lane 1 again.
    assert rows[3].column == 1
