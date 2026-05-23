"""`briar plan` — build sequenced implementation plans from a tracker
board.

Public surface:

  * `ImplementationPlan` / `PlanCard` — data shapes
  * `build_plan` — fetch a board, enrich each card, sort by deps,
                   optionally chain branches in cascade mode
  * `save_plan` / `load_plan` / `list_plans` / `delete_plan` — round-trip
                   plans through any `KnowledgeStore`
  * `BoardReader` / `BoardReaderRegistry` — Strategy + Registry for
                   board URL adapters (Jira, GitHub Projects v2; new
                   ones land as one module under `_boards/`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from briar.agent._llm import LLMProvider
from briar.plan._board import BoardReader, BoardRef
from briar.plan._boards import (
    BOARD_READERS,
    BoardReaderRegistry,
    resolve_board,
)
from briar.plan._graph import apply_cascade, topological_sort
from briar.plan._models import (
    PLAN_SCHEMA_VERSION,
    ImplementationPlan,
    PlanCard,
)
from briar.plan._store import (
    blob_name_for,
    delete_plan,
    list_plans,
    load_plan,
    render_markdown,
    save_plan,
)
from briar.plan._synthesize import (
    CardSynthesiser,
    HeuristicSynthesiser,
    LLMSynthesiser,
    make_synthesiser,
)


log = logging.getLogger(__name__)


def build_plan(
    *,
    board_url: str,
    name: str,
    company: str = "",
    cascade: bool = False,
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
      4. Topologically sort by `depends_on`.
      5. Assign branch names; in cascade mode chain parents through
         the topological order.

    Returns a fully populated `ImplementationPlan`. The caller chooses
    whether to persist it.
    """
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

    ordered = topological_sort(cards)
    ordered = apply_cascade(ordered, cascade=cascade, default_branch=default_branch)

    plan = ImplementationPlan(
        name=name,
        board_url=board_url,
        tracker=ref.tracker,
        project=ref.project,
        cascade=cascade,
        company=company,
        owner=ref.owner,
        repo=ref.repo,
        default_branch=default_branch,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        cards=ordered,
    )
    log.info(
        "plan: built name=%s cards=%d cascade=%s tracker=%s project=%s",
        name,
        len(ordered),
        cascade,
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
    "LLMSynthesiser",
    "ImplementationPlan",
    "PlanCard",
    "apply_cascade",
    "blob_name_for",
    "build_plan",
    "delete_plan",
    "list_plans",
    "load_plan",
    "make_synthesiser",
    "render_markdown",
    "resolve_board",
    "save_plan",
    "topological_sort",
]
