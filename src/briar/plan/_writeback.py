"""After a card succeeds, ask the LLM to update the plan-scoped
knowledge blob so the next selector call sees what was learned.

`knowledge:<company>.<plan>` is the live source of truth. It is seeded
at `plan build` time with a plan summary, then merged after each
successful card by the writer here. The implement agent's
`KnowledgeSplicer` already concatenates every blob whose name starts
with `knowledge:<company>` (see `iac/scaffold/_knowledge.py:57`), so
this blob is automatically visible to subsequent `agent implement`
calls without any extra wiring.

One concrete class. No ABC — there is one implementation. If a no-op
or in-memory writer ever shows up for tests, lift to ABC + factory
mirroring `_synthesize.py`."""

from __future__ import annotations

import logging
from typing import List

from briar.agent._llm import LLMProvider
from briar.errors import CliError
from briar.plan._json_utils import extract_json
from briar.plan._models import ImplementationPlan, PlanCard
from briar.storage import KnowledgeStore

log = logging.getLogger(__name__)


class KnowledgeWriter:
    """Merge per-card learnings into `knowledge:<company>.<plan>`.

    The LLM is given the prior body, the card metadata, and a bounded
    diff/summary of the work just done; it returns the new full body
    of the blob. `put_if_changed` short-circuits no-op writes."""

    SYSTEM = (
        "You maintain a living markdown knowledge document for an engineering plan. "
        "Given the prior document, a card that was just completed, and a summary of "
        "what changed, return the NEW full markdown body. Rules:\n"
        "- Preserve information that is still true; remove or update what is now stale.\n"
        "- Add concise notes on new facts, decisions, gotchas, and follow-ups.\n"
        "- Keep total length under ~4KB; prefer terse bullets over prose.\n"
        '- Return STRICT JSON: {"body":"<new full markdown body>"}. '
        "Do not include code fences around the JSON."
    )

    def __init__(self, llm: LLMProvider, *, max_tokens: int = 2000) -> None:
        if not llm.is_available():
            raise CliError("writeback requires an available LLM provider")
        self._llm = llm
        self._max_tokens = max_tokens

    def write(
        self,
        *,
        store: KnowledgeStore,
        plan: ImplementationPlan,
        card: PlanCard,
        diff: str,
    ) -> bool:
        """Merge learnings from one completed card. Returns True if the
        blob was rewritten, False if the model returned the same body
        (or anything unusable — failure to merge is non-fatal)."""
        if not (plan.company and plan.name):
            log.info("writeback: skipped (plan missing company/name)")
            return False
        blob_name = f"knowledge:{plan.company}.{plan.name}"
        prior = store.get(blob_name) or ""
        prompt = self._build_prompt(plan, card, diff, prior)
        try:
            response = self._llm.complete(
                system=self.SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                max_tokens=self._max_tokens,
            )
        except Exception:  # noqa: BLE001 — writeback is best-effort
            log.exception("writeback: LLM call failed for card=%s", card.key)
            return False
        payload = extract_json(response.text)
        if not payload:
            log.warning("writeback: unparseable response for card=%s", card.key)
            return False
        new_body = str(payload.get("body") or "").strip()
        if not new_body:
            log.warning("writeback: empty body for card=%s", card.key)
            return False
        result = store.put_if_changed(blob_name, new_body, category="knowledge")
        log.info(
            "writeback: blob=%s wrote=%s bytes=%d card=%s",
            blob_name,
            result.wrote,
            result.byte_count,
            card.key,
        )
        return bool(result.wrote)

    @staticmethod
    def _build_prompt(plan: ImplementationPlan, card: PlanCard, diff: str, prior: str) -> str:
        parts: List[str] = []
        parts.append(f"Plan: {plan.name}  (company: {plan.company})")
        parts.append("")
        parts.append("CARD JUST COMPLETED")
        parts.append(f"  key: {card.key}")
        parts.append(f"  title: {card.title}")
        if card.summary:
            parts.append(f"  summary: {card.summary[:400]}")
        if card.in_scope:
            parts.append(f"  in_scope: {'; '.join(card.in_scope[:5])}")
        parts.append("")
        parts.append("CHANGE SUMMARY (diff / commit / agent notes — possibly truncated):")
        parts.append(diff[:4000] if diff else "(none)")
        parts.append("")
        parts.append("PRIOR KNOWLEDGE BLOB:")
        parts.append(prior or "(empty)")
        parts.append("")
        parts.append('Return STRICT JSON: {"body":"<new markdown body>"}')
        return "\n".join(parts)
