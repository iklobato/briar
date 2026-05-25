"""Plan dependency graph — topological sort + cascade branch assignment."""

from __future__ import annotations

import pytest

from briar.errors import CliError
from briar.plan._graph import _suggest_branch, apply_cascade, topological_sort
from briar.plan._models import PlanCard


def _card(key: str, deps: list[str] = (), branch: str = "") -> PlanCard:
    return PlanCard(key=key, title=key, depends_on=list(deps), branch_name=branch)


class TestTopologicalSort:
    def test_empty_list_returns_empty(self) -> None:
        assert topological_sort([]) == []

    def test_no_deps_preserves_input_order(self) -> None:
        cards = [_card("A"), _card("B"), _card("C")]
        out = topological_sort(cards)
        assert [c.key for c in out] == ["A", "B", "C"]

    def test_linear_chain_orders_by_dep(self) -> None:
        # B depends on A; C depends on B → A, B, C
        cards = [_card("C", ["B"]), _card("B", ["A"]), _card("A")]
        out = topological_sort(cards)
        assert [c.key for c in out] == ["A", "B", "C"]

    def test_unknown_deps_silently_trimmed(self) -> None:
        # A depends on EXT (not in board) → drops dep, keeps A
        cards = [_card("A", ["EXT"])]
        out = topological_sort(cards)
        assert [c.key for c in out] == ["A"]
        assert out[0].depends_on == []  # trimmed

    def test_self_dependency_dropped(self) -> None:
        # A depends on itself → dep dropped, no cycle
        cards = [_card("A", ["A"])]
        out = topological_sort(cards)
        assert [c.key for c in out] == ["A"]
        assert out[0].depends_on == []

    def test_cycle_raises_clierror(self) -> None:
        # A → B → A
        cards = [_card("A", ["B"]), _card("B", ["A"])]
        with pytest.raises(CliError, match="cycle"):
            topological_sort(cards)

    def test_diamond_order_deterministic(self) -> None:
        # A → B, A → C, B & C → D
        cards = [_card("D", ["B", "C"]), _card("C", ["A"]), _card("B", ["A"]), _card("A")]
        out = topological_sort(cards)
        keys = [c.key for c in out]
        # A first; D last; B,C in between (order depends on input)
        assert keys[0] == "A"
        assert keys[-1] == "D"
        assert set(keys[1:-1]) == {"B", "C"}


class TestApplyCascade:
    def test_cascade_off_all_use_default_branch(self) -> None:
        cards = [_card("A"), _card("B", ["A"])]
        out = apply_cascade(cards, cascade=False, default_branch="main")
        assert all(c.branch_parent == "main" for c in out)

    def test_cascade_on_uses_latest_dep_branch(self) -> None:
        # B depends on A; A's branch is briar/a → B's parent = briar/a
        a = _card("A", branch="briar/a")
        b = _card("B", ["A"])
        out = apply_cascade([a, b], cascade=True, default_branch="main")
        b_out = next(c for c in out if c.key == "B")
        assert b_out.branch_parent == "briar/a"

    def test_cascade_no_deps_uses_default(self) -> None:
        cards = [_card("A")]
        out = apply_cascade(cards, cascade=True, default_branch="main")
        assert out[0].branch_parent == "main"

    def test_cascade_picks_latest_in_sort_order(self) -> None:
        # B depends on A and C; A appears before C in the list → C wins
        # (cascade picks the dep with highest index in the input order).
        a = _card("A", branch="briar/a")
        c = _card("C", branch="briar/c")
        b = _card("B", ["A", "C"])
        out = apply_cascade([a, c, b], cascade=True, default_branch="main")
        b_out = next(x for x in out if x.key == "B")
        assert b_out.branch_parent == "briar/c"

    def test_missing_branch_name_synthesised(self) -> None:
        cards = [_card("KAN-12")]
        out = apply_cascade(cards, cascade=False, default_branch="main")
        assert out[0].branch_name.startswith("briar/")


class TestSuggestBranch:
    @pytest.mark.parametrize("key,expected", [
        ("KAN-12", "briar/kan-12"),
        ("#42", "briar/issue-42"),
        ("FOO 123", "briar/foo-123"),
        ("PROJ/BUG-1", "briar/proj-bug-1"),
        ("", "briar/card"),
    ])
    def test_branch_slug(self, key: str, expected: str) -> None:
        assert _suggest_branch(key) == expected
