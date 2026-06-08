"""Selector — the branches `tests/test_plan.py::SelectorTests` leaves
uncovered: the LLM-call-fails path, the branch_parent carry-through on
`pick`, and the prompt-building branches (completed/failed-with-last-attempt
/ in-progress / knowledge sections).

The selector validates the model's pick against the plan's pending keys;
these tests assert the *decision values* the selector produced and the
*prompt content* it built — not that the LLM mock was called.
"""

from __future__ import annotations

import json

import pytest

from briar.errors import CliError
from briar.plan._enums import PlanCardStatus, SelectorActionKind
from briar.plan._models import ImplementationPlan, PlanCard, PlanContext
from briar.plan._selector import Selector


class _CapturingLLM:
    """Records the prompt; returns a canned text (or raises)."""

    kind = "cap"

    def __init__(self, *, text="", raises=None):
        self._text = text
        self._raises = raises
        self.last_prompt = None
        self.last_system = None

    def is_available(self):
        return True

    def complete(self, *, system, messages, tools, max_tokens):
        self.last_system = system
        self.last_prompt = messages[0]["content"]
        if self._raises:
            raise self._raises
        from briar.agent._llm import LLMResponse

        return LLMResponse(text=self._text, tool_calls=[], stop_reason="end_turn", input_tokens=0, output_tokens=0)


def _plan(*cards: PlanCard, name="demo", company="acme") -> ImplementationPlan:
    return ImplementationPlan(name=name, board_url="jira:KAN", tracker="jira", project="KAN", company=company, cards=list(cards))


class TestDecision:
    def test_llm_failure_wrapped_as_clierror(self):
        plan = _plan(PlanCard(key="A", title="a"))
        llm = _CapturingLLM(raises=TimeoutError("upstream timeout"))
        with pytest.raises(CliError, match="selector LLM call failed"):
            Selector(llm).pick(plan, PlanContext())

    def test_pick_carries_branch_parent(self):
        plan = _plan(PlanCard(key="A", title="a"), PlanCard(key="B", title="b"))
        llm = _CapturingLLM(text=json.dumps({"action": "pick", "key": "B", "why": "stack", "branch_parent": "develop"}))
        decision = Selector(llm).pick(plan, PlanContext())
        assert decision.kind is SelectorActionKind.PICK
        assert decision.key == "B"
        assert decision.branch_parent == "develop"

    def test_why_truncated_to_500(self):
        plan = _plan(PlanCard(key="A", title="a"))
        llm = _CapturingLLM(text=json.dumps({"action": "blocked", "why": "z" * 1000}))
        decision = Selector(llm).pick(plan, PlanContext())
        assert decision.kind is SelectorActionKind.BLOCKED
        assert len(decision.why) == 500

    def test_pick_with_done_card_key_rejected(self):
        # The model may only pick a PENDING card; a done card's key is invalid.
        plan = _plan(
            PlanCard(key="A", title="a", status=PlanCardStatus.DONE),
            PlanCard(key="B", title="b"),
        )
        llm = _CapturingLLM(text=json.dumps({"action": "pick", "key": "A"}))
        with pytest.raises(CliError, match="not in pending"):
            Selector(llm).pick(plan, PlanContext())


class TestPromptContent:
    def test_prompt_includes_completed_failed_and_last_attempt(self):
        plan = _plan(
            PlanCard(key="B", title="beta", status=PlanCardStatus.BLOCKED, last_attempt_summary="segfault in parser"),
            PlanCard(key="C", title="gamma"),
        )
        ctx = PlanContext(
            completed=[("A", "did A")],
            failed=[("B", "B broke")],
            in_progress="X",
            knowledge="PLAN-LORE",
            company_knowledge="CO-LORE",
        )
        llm = _CapturingLLM(text=json.dumps({"action": "pick", "key": "C", "why": "next"}))
        Selector(llm).pick(plan, ctx)
        prompt = llm.last_prompt
        assert "COMPLETED (1):" in prompt
        assert "- A: did A" in prompt
        assert "FAILED (1):" in prompt
        assert "- B: B broke" in prompt
        assert "last_attempt: segfault in parser" in prompt
        assert "IN PROGRESS: X" in prompt
        assert "PLAN-LORE" in prompt
        assert "CO-LORE" in prompt
        # Only the pending card (C) is listed under PENDING.
        assert "- C: gamma" in prompt

    def test_prompt_handles_empty_context(self):
        plan = _plan(PlanCard(key="A", title="a"))
        llm = _CapturingLLM(text=json.dumps({"action": "pick", "key": "A"}))
        Selector(llm).pick(plan, PlanContext())
        prompt = llm.last_prompt
        assert "COMPLETED (0):" in prompt
        assert "FAILED (0):" in prompt
        assert "IN PROGRESS: (none)" in prompt

    def test_no_pending_short_circuits_without_calling_llm(self):
        plan = _plan(PlanCard(key="A", title="a", status=PlanCardStatus.DONE))
        llm = _CapturingLLM(raises=AssertionError("LLM must not be called"))
        decision = Selector(llm).pick(plan, PlanContext())
        assert decision.kind is SelectorActionKind.COMPLETE
        assert llm.last_prompt is None
