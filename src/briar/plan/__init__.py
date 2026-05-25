"""`briar plan` — build, inspect, and execute LLM-driven implementation
plans from a tracker board.

Public surface:

  * `ImplementationPlan` / `PlanCard` — data shapes
  * `build_plan` — fetch a board, enrich each card via synthesis, persist
  * `replan` — re-derive the card list from the board, preserving
                statuses of overlapping keys
  * `save_plan` / `load_plan` / `list_plans` / `delete_plan` —
                round-trip plans through any `KnowledgeStore`
  * `render_plan_knowledge` — seed body for the plan-scoped knowledge
                blob written at build time
  * `Selector` — LLM-driven next-card picker, returns `SelectorDecision`
  * `KnowledgeWriter` — post-card writer that maintains
                `knowledge:<company>.<plan>` as the live source of truth
  * `collect_status` / `render_table` — `briar plan status` projection
  * `BoardReader` / `BoardReaderRegistry` — Strategy + Registry for
                board URL adapters (Jira, GitHub Projects v2)

Removed in this version: `topological_sort`, `apply_cascade`, the
`--cascade` flag, and `ImplementationPlan.next_pending()`. The
dependency-graph picker is gone; the LLM selector takes its place
and `depends_on` is now read as a hint, not enforced as a gate."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from briar.agent._llm import LLMProvider
from briar.plan._board import BoardReader, BoardRef
from briar.plan._boards import BOARD_READERS, BoardReaderRegistry, resolve_board
from briar.plan._enums import PlanCardStatus, SelectorActionKind
from briar.plan._models import PLAN_SCHEMA_VERSION, ImplementationPlan, PlanCard, PlanContext, SelectorDecision, suggest_branch
from briar.plan._replan import replan
from briar.plan._selector import Selector
from briar.plan._status import collect_status, render_table
from briar.plan._store import blob_name_for, delete_plan, list_plans, load_plan, render_markdown, render_plan_knowledge, save_plan
from briar.plan._synthesize import CardSynthesiser, HeuristicSynthesiser, LLMSynthesiser, make_synthesiser
from briar.plan._writeback import KnowledgeWriter

log = logging.getLogger(__name__)


def build_plan(
    *,
    board_url: str,
    name: str,
    company: str = "",
    default_branch: str = "main",
    max_cards: int = 50,
    llm: Optional[LLMProvider] = None,
    context_sections: Optional[List[str]] = None,
    reader: Optional[BoardReader] = None,
) -> ImplementationPlan:
    """End-to-end plan synthesis. Steps:

      1. Resolve the URL → `BoardReader` and `BoardRef`.
      2. Fetch raw cards from the tracker.
      3. Enrich each card via the LLM (when available) + heuristic pass.
      4. Assign a default branch name + parent for each card.

    There is no topological sort: cards are kept in board order, and
    the LLM selector picks freely at run time. `branch_parent` defaults
    to `default_branch` for every card; the selector may override per
    pick.

    Returns a fully populated `ImplementationPlan`. The caller chooses
    whether to persist it."""
    reader = reader or resolve_board(board_url)
    ref = reader.parse(board_url)
    cards = reader.fetch(ref, company=company, max_cards=max_cards)
    if not cards:
        log.warning("plan: board returned no cards (board=%s project=%s)", board_url, ref.project)

    board_keys = [c.key for c in cards]
    synthesiser = make_synthesiser(llm)
    context = list(context_sections or [])
    for card in cards:
        synthesiser.enrich(card, board_card_keys=board_keys, context_sections=context)
        if not card.branch_name:
            card.branch_name = suggest_branch(card.key)
        if not card.branch_parent:
            card.branch_parent = default_branch

    plan = ImplementationPlan(
        name=name,
        board_url=board_url,
        tracker=ref.tracker,
        project=ref.project,
        company=company,
        owner=ref.owner,
        repo=ref.repo,
        default_branch=default_branch,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        cards=cards,
    )
    log.info(
        "plan: built name=%s cards=%d tracker=%s project=%s",
        name,
        len(cards),
        ref.tracker,
        ref.project,
    )
    return plan


__all__ = [
    "PLAN_SCHEMA_VERSION",
    "BoardReader",
    "BoardReaderRegistry",
    "BoardRef",
    "BOARD_READERS",
    "CardSynthesiser",
    "HeuristicSynthesiser",
    "ImplementationPlan",
    "KnowledgeWriter",
    "LLMSynthesiser",
    "PlanCard",
    "PlanCardStatus",
    "PlanContext",
    "Selector",
    "SelectorActionKind",
    "SelectorDecision",
    "blob_name_for",
    "build_plan",
    "collect_status",
    "delete_plan",
    "list_plans",
    "load_plan",
    "make_synthesiser",
    "render_markdown",
    "render_plan_knowledge",
    "render_table",
    "replan",
    "resolve_board",
    "save_plan",
    "suggest_branch",
]
