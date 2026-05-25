"""LLM-driven next-card picker.

Replaces the deleted `ImplementationPlan.next_pending()` algorithm and
the topological-sort-then-take-head pipeline that lived in `_graph.py`.
The selector is the single seam where "what should we do next?" turns
into a structured `SelectorDecision` the runner can match on.

One concrete class. No ABC (only one implementation in the codebase),
no factory (the runner constructs it directly with its `LLMProvider`).
If a second implementation ever shows up — for instance, a recorded-
replay selector for tests that doesn't hit the network — the ABC + the
factory go in together, mirroring the `_synthesize.py` precedent.

The LLM's freedom is bounded by validation on the way back: a picked
`key` MUST be a pending card on the plan; otherwise the call raises
and the runner records `plan.next.invalid` in the journal."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from briar.agent._llm import LLMProvider
from briar.errors import CliError
from briar.plan._enums import PlanCardStatus, SelectorActionKind
from briar.plan._models import ImplementationPlan, PlanCard, PlanContext, SelectorDecision

log = logging.getLogger(__name__)


class Selector:
    """Ask the model what to do next, parse the JSON, validate, return.

    The prompt shape is explicit and bounded: completed cards, failed
    cards with the last attempt summary, the current in-flight card if
    any, every pending card with its summary/in-scope/risks, the
    knowledge blob (plan-scoped + company-scoped), and the closed list
    of allowed actions. The model returns one JSON object describing
    its decision."""

    SYSTEM = (
        "You are the planner for an engineering agent. Given the past work, "
        "the in-flight work, the pending cards, and the accumulated knowledge "
        "blob, decide what should happen next. Return STRICT JSON with one of "
        "these shapes:\n"
        '  {"action":"pick","key":"<pending-card-key>","why":"<short>","branch_parent":"<optional>"}\n'
        '  {"action":"replan","why":"<why the current card list is stale>"}\n'
        '  {"action":"complete","why":"<short>"}\n'
        '  {"action":"blocked","why":"<short>"}\n'
        "Rules:\n"
        "- `key` for `pick` MUST be exactly one of the pending card keys provided.\n"
        "- Prefer `pick` over `replan`; only return `replan` when the listed cards "
        "no longer reflect reality (board has moved, scope has shifted, etc.).\n"
        "- `depends_on` is a hint, not a gate — you may pick a card whose deps "
        "aren't done if doing so unblocks more work, but justify it in `why`.\n"
        "- Return ONLY the JSON object, no prose, no code fences."
    )

    def __init__(self, llm: LLMProvider, *, max_tokens: int = 800) -> None:
        if not llm.is_available():
            raise CliError("selector requires an available LLM provider")
        self._llm = llm
        self._max_tokens = max_tokens

    def pick(self, plan: ImplementationPlan, ctx: PlanContext) -> SelectorDecision:
        pending = [c for c in plan.cards if c.status == PlanCardStatus.PENDING]
        if not pending:
            return SelectorDecision(kind=SelectorActionKind.COMPLETE, why="no pending cards")

        prompt = self._build_prompt(plan, ctx, pending)
        try:
            response = self._llm.complete(
                system=self.SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=self._max_tokens,
            )
        except Exception as exc:  # noqa: BLE001 — surface as a CliError; runner journals
            raise CliError(f"selector LLM call failed: {exc}") from exc

        payload = _extract_json(response.text)
        if not payload:
            raise CliError(f"selector returned unparseable response: {response.text!r}")

        return self._parse_decision(payload, pending)

    @staticmethod
    def _parse_decision(payload: Dict[str, Any], pending: List[PlanCard]) -> SelectorDecision:
        action = str(payload.get("action") or "").strip().lower()
        try:
            kind = SelectorActionKind(action)
        except ValueError as exc:
            raise CliError(f"selector returned unknown action: {action!r}") from exc

        why = str(payload.get("why") or "")[:500]
        if kind is SelectorActionKind.PICK:
            key = str(payload.get("key") or "").strip()
            pending_keys = {c.key for c in pending}
            if key not in pending_keys:
                raise CliError(f"selector picked key {key!r}, not in pending {sorted(pending_keys)}")
            branch_parent = str(payload.get("branch_parent") or "")
            return SelectorDecision(kind=kind, key=key, why=why, branch_parent=branch_parent)
        return SelectorDecision(kind=kind, why=why)

    @staticmethod
    def _build_prompt(plan: ImplementationPlan, ctx: PlanContext, pending: List[PlanCard]) -> str:
        parts: List[str] = []
        parts.append(f"Plan: {plan.name}")
        parts.append(f"Board: {plan.board_url or '(none)'}")
        if plan.company:
            parts.append(f"Company: {plan.company}")
        parts.append("")
        parts.append(f"COMPLETED ({len(ctx.completed)}):")
        if ctx.completed:
            for key, rationale in ctx.completed[-20:]:
                parts.append(f"  - {key}: {rationale}")
        else:
            parts.append("  (none)")
        parts.append("")
        parts.append(f"FAILED ({len(ctx.failed)}):")
        if ctx.failed:
            for key, rationale in ctx.failed[-10:]:
                parts.append(f"  - {key}: {rationale}")
                card = next((c for c in plan.cards if c.key == key), None)
                if card and card.last_attempt_summary:
                    parts.append(f"      last_attempt: {card.last_attempt_summary[:300]}")
        else:
            parts.append("  (none)")
        parts.append("")
        parts.append(f"IN PROGRESS: {ctx.in_progress or '(none)'}")
        parts.append("")
        parts.append(f"PENDING ({len(pending)}):")
        for c in pending:
            line = f"  - {c.key}: {c.title}"
            if c.depends_on:
                line += f"  [depends_on: {', '.join(c.depends_on)}]"
            parts.append(line)
            if c.summary:
                parts.append(f"      summary: {c.summary[:240]}")
            if c.in_scope:
                parts.append(f"      in_scope: {'; '.join(c.in_scope[:5])}")
            if c.risks:
                parts.append(f"      risks: {'; '.join(c.risks[:3])}")
        parts.append("")
        if ctx.knowledge:
            parts.append("PLAN KNOWLEDGE (knowledge:<company>.<plan>):")
            parts.append(ctx.knowledge)
            parts.append("")
        if ctx.company_knowledge:
            parts.append("COMPANY KNOWLEDGE (knowledge:<company>):")
            parts.append(ctx.company_knowledge)
            parts.append("")
        parts.append("Return STRICT JSON only.")
        return "\n".join(parts)


# Reused by `_writeback.py` and tests; mirrors `_synthesize._extract_json`
# but kept local so the selector module doesn't reach into a sibling's
# private helper. Once a third caller wants this, lift it into a shared
# `_json_utils.py`; until then, two ~10-line copies are the right cost.
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    candidate = text.strip()
    fenced = _FENCE_RE.match(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        try:
            data = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None
