"""Plan persistence — round-trip `ImplementationPlan` through any
`KnowledgeStore` backend.

A plan is stored as a single blob named `plan:<plan-name>`. The blob
body is a fenced markdown block holding JSON so a human reading the
file backend sees the rendered structure, and any backend that lists
by prefix can enumerate plans with `store.list(prefix="plan:")`.

This module owns the wire format. Anyone serialising or deserialising
a plan from a store should go through `save_plan` / `load_plan` so we
don't grow drift across call sites."""

from __future__ import annotations

import json
import logging
import re
from typing import List

from briar.errors import CliError
from briar.plan._models import ImplementationPlan
from briar.storage import KnowledgeStore

log = logging.getLogger(__name__)


_BLOB_PREFIX = "plan:"
_FENCE_RE = re.compile(r"```json\s*\n(?P<body>.*?)\n```", re.DOTALL)


def blob_name_for(plan_name: str) -> str:
    if not plan_name:
        raise CliError("plan name required")
    safe = plan_name.strip().replace(" ", "-")
    if safe.startswith(_BLOB_PREFIX):
        return safe
    return f"{_BLOB_PREFIX}{safe}"


def save_plan(store: KnowledgeStore, plan: ImplementationPlan) -> str:
    """Persist `plan` and return the blob name it was stored under."""
    name = blob_name_for(plan.name)
    body = render_markdown(plan)
    store.put(name, body, category="plan")
    log.info("plan: saved %s (%d cards)", name, len(plan.cards))
    return name


def load_plan(store: KnowledgeStore, plan_name: str) -> ImplementationPlan:
    """Read a plan from the store. Raises `CliError` when the blob is
    missing or the JSON cannot be parsed."""
    name = blob_name_for(plan_name)
    body = store.get(name)
    if not body:
        raise CliError(f"plan not found: {name}")
    payload = _extract_payload(body)
    if not payload:
        raise CliError(f"plan blob {name!r} did not contain a JSON block")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CliError(f"plan blob {name!r} has malformed JSON: {exc}") from exc
    return ImplementationPlan.from_dict(data)


def list_plans(store: KnowledgeStore) -> List[str]:
    """Return the blob names of every stored plan."""
    return [ref.name for ref in store.list(prefix=_BLOB_PREFIX)]


def delete_plan(store: KnowledgeStore, plan_name: str) -> bool:
    """Remove a plan from the store. Returns True when a row was
    deleted, False when nothing matched."""
    return bool(store.delete(blob_name_for(plan_name)))


def render_markdown(plan: ImplementationPlan) -> str:
    """Human-readable markdown wrapper around the canonical JSON
    payload. Order: header + summary table + ordered card list + raw
    JSON in a fenced block so reload is lossless."""
    lines: List[str] = []
    lines.append(f"# Plan — {plan.name}")
    lines.append("")
    lines.append(f"- Board: {plan.board_url or '(none)'}")
    lines.append(f"- Tracker: {plan.tracker}")
    lines.append(f"- Project: {plan.project}")
    if plan.company:
        lines.append(f"- Company: {plan.company}")
    lines.append(f"- Default branch: {plan.default_branch}")
    if plan.created_at:
        lines.append(f"- Created: {plan.created_at}")
    lines.append("")
    lines.append("## Ordered cards")
    lines.append("")
    for i, card in enumerate(plan.cards, start=1):
        lines.append(f"### {i}. {card.key} — {card.title}")
        if card.depends_on:
            lines.append(f"- Depends on: {', '.join(card.depends_on)}")
        lines.append(f"- Branch: `{card.branch_name}` (from `{card.branch_parent}`)")
        lines.append(f"- Status: {card.status}")
        if card.url:
            lines.append(f"- URL: {card.url}")
        if card.summary:
            lines.append("")
            lines.append(card.summary)
        if card.in_scope:
            lines.append("")
            lines.append("**In scope**")
            for item in card.in_scope:
                lines.append(f"- {item}")
        if card.out_of_scope:
            lines.append("")
            lines.append("**Out of scope**")
            for item in card.out_of_scope:
                lines.append(f"- {item}")
        if card.risks:
            lines.append("")
            lines.append("**Risks / open questions**")
            for item in card.risks:
                lines.append(f"- {item}")
        if card.sources:
            lines.append("")
            lines.append(f"_Sources: {', '.join(card.sources)}_")
        lines.append("")
    lines.append("## Raw plan payload")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(plan.to_dict(), indent=2))
    lines.append("```")
    return "\n".join(lines)


def render_plan_knowledge(plan: ImplementationPlan) -> str:
    """Seed body for `knowledge:<company>.<plan>`.

    Written by `BuildOp` immediately after `save_plan`. Captures the
    board context, the per-card scope, and the cards' titles/summaries
    so the very first `plan next --llm` invocation has substrate to
    reason over before any card has run. Subsequent runs grow this
    blob via `KnowledgeWriter`."""
    lines: List[str] = []
    lines.append(f"# {plan.name} — plan knowledge")
    lines.append("")
    if plan.company:
        lines.append(f"- Company: {plan.company}")
    lines.append(f"- Board: {plan.board_url or '(none)'}")
    lines.append(f"- Tracker: {plan.tracker}")
    if plan.project:
        lines.append(f"- Project: {plan.project}")
    if plan.owner and plan.repo:
        lines.append(f"- Repo: {plan.owner}/{plan.repo}")
    lines.append(f"- Default branch: {plan.default_branch}")
    if plan.created_at:
        lines.append(f"- Seeded: {plan.created_at}")
    lines.append("")
    lines.append("## Cards")
    lines.append("")
    for c in plan.cards:
        lines.append(f"### {c.key} — {c.title}")
        if c.summary:
            lines.append(c.summary)
        if c.in_scope:
            lines.append("")
            lines.append("**In scope**")
            for item in c.in_scope:
                lines.append(f"- {item}")
        if c.out_of_scope:
            lines.append("")
            lines.append("**Out of scope**")
            for item in c.out_of_scope:
                lines.append(f"- {item}")
        if c.risks:
            lines.append("")
            lines.append("**Risks / open questions**")
            for item in c.risks:
                lines.append(f"- {item}")
        if c.depends_on:
            lines.append("")
            lines.append(f"_Depends on: {', '.join(c.depends_on)}_")
        lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("_This document is updated by the agent after each completed card._")
    return "\n".join(lines)


def _extract_payload(body: str) -> str:
    """Pull the JSON block back out of the markdown wrapper."""
    match = _FENCE_RE.search(body)
    if match:
        return match.group("body")
    # Fallback for plans stored as raw JSON.
    stripped = body.strip()
    if stripped.startswith("{"):
        return stripped
    return ""
