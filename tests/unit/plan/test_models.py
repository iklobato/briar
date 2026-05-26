"""ImplementationPlan + PlanCard — dataclasses, serialisation, and the
new value objects (SelectorDecision, PlanContext)."""

from __future__ import annotations

from briar.plan._enums import PlanCardStatus, SelectorActionKind
from briar.plan._models import ImplementationPlan, PlanCard, PlanContext, SelectorDecision, suggest_branch


def _card(key: str, status: PlanCardStatus = PlanCardStatus.PENDING, deps: list[str] = ()) -> PlanCard:
    return PlanCard(key=key, title=key, status=status, depends_on=list(deps))


def _plan(cards: list[PlanCard]) -> ImplementationPlan:
    return ImplementationPlan(name="p", board_url="", tracker="", project="", cards=cards)


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
        c = PlanCard.from_dict({"key": "A", "title": "Title"})
        assert c.key == "A"
        assert c.depends_on == []
        assert c.status == PlanCardStatus.PENDING
        assert c.last_attempt_summary == ""

    def test_last_attempt_summary_roundtrip(self) -> None:
        c = PlanCard(key="A", title="a", last_attempt_summary="boom")
        roundtrip = PlanCard.from_dict(c.to_dict())
        assert roundtrip.last_attempt_summary == "boom"

    def test_legacy_cascade_field_ignored(self) -> None:
        """Pre-v2 plan dicts had a `cascade` field; from_dict must accept it."""
        raw = {
            "version": 1,
            "name": "old",
            "board_url": "",
            "tracker": "jira",
            "project": "X",
            "cascade": True,
            "cards": [],
        }
        plan = ImplementationPlan.from_dict(raw)
        assert plan.name == "old"
        assert not hasattr(plan, "cascade")


class TestSuggestBranch:
    def test_jira_key(self) -> None:
        assert suggest_branch("KAN-12") == "chore/kan-12"

    def test_issue_hash(self) -> None:
        assert suggest_branch("#42") == "chore/issue-42"


class TestSelectorDecision:
    def test_default_kind_only(self) -> None:
        d = SelectorDecision(kind=SelectorActionKind.COMPLETE)
        assert d.kind is SelectorActionKind.COMPLETE
        assert d.key == ""
        assert d.why == ""

    def test_pick_fields(self) -> None:
        d = SelectorDecision(kind=SelectorActionKind.PICK, key="A", why="reason", branch_parent="develop")
        assert d.key == "A"
        assert d.branch_parent == "develop"


class TestPlanContext:
    def test_empty_default(self) -> None:
        ctx = PlanContext()
        assert ctx.completed == []
        assert ctx.failed == []
        assert ctx.in_progress is None
        assert ctx.knowledge == ""
