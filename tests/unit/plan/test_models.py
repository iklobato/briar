"""ImplementationPlan + PlanCard — dataclasses + next_pending logic."""

from __future__ import annotations

import pytest

from briar.plan._enums import PlanCardStatus
from briar.plan._models import ImplementationPlan, PlanCard


def _card(key: str, status: PlanCardStatus = PlanCardStatus.PENDING, deps: list[str] = ()) -> PlanCard:
    return PlanCard(key=key, title=key, status=status, depends_on=list(deps))


def _plan(cards: list[PlanCard]) -> ImplementationPlan:
    return ImplementationPlan(name="p", board_url="", tracker="", project="", cards=cards)


class TestNextPending:
    def test_empty_plan_returns_none(self) -> None:
        assert _plan([]).next_pending() is None

    def test_first_pending_returned(self) -> None:
        cards = [_card("A"), _card("B")]
        assert _plan(cards).next_pending().key == "A"

    def test_in_progress_skipped(self) -> None:
        cards = [_card("A", PlanCardStatus.IN_PROGRESS), _card("B")]
        assert _plan(cards).next_pending().key == "B"

    def test_done_skipped(self) -> None:
        cards = [_card("A", PlanCardStatus.DONE), _card("B")]
        assert _plan(cards).next_pending().key == "B"

    def test_card_with_unsatisfied_dep_skipped(self) -> None:
        # B depends on A; A is pending → B not ready yet
        cards = [_card("A"), _card("B", deps=["A"])]
        # next_pending returns A first (its deps are empty).
        nxt = _plan(cards).next_pending()
        assert nxt.key == "A"

    def test_card_ready_when_deps_done(self) -> None:
        cards = [_card("A", PlanCardStatus.DONE), _card("B", deps=["A"])]
        assert _plan(cards).next_pending().key == "B"

    def test_all_done_returns_none(self) -> None:
        cards = [_card("A", PlanCardStatus.DONE)]
        assert _plan(cards).next_pending() is None


class TestRoundtrip:
    def test_plan_to_dict_from_dict_equal(self) -> None:
        p = _plan([_card("A"), _card("B", deps=["A"])])
        p.created_at = "2026-01-01"
        roundtrip = ImplementationPlan.from_dict(p.to_dict())
        assert roundtrip.name == p.name
        assert len(roundtrip.cards) == 2
        assert roundtrip.cards[1].depends_on == ["A"]

    def test_card_status_roundtrip(self) -> None:
        c = _card("A", PlanCardStatus.IN_PROGRESS)
        roundtrip = PlanCard.from_dict(c.to_dict())
        assert roundtrip.status == PlanCardStatus.IN_PROGRESS

    def test_from_dict_handles_missing_fields(self) -> None:
        # Sparse dict — only `key` and `title` provided.
        c = PlanCard.from_dict({"key": "A", "title": "Title"})
        assert c.key == "A"
        assert c.depends_on == []
        assert c.status == PlanCardStatus.PENDING
