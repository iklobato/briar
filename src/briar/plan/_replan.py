"""Re-derive the card list when the LLM selector says the world has
drifted enough.

`replan` is `build_plan(...)` with status preservation: the freshly
fetched board produces a new ordered card list, and any card key that
already existed in the old plan carries its `status` (and any captured
`last_attempt_summary`) forward. Cards that vanished from the board
disappear; cards that appeared are new and start `PENDING`.

This is a pure helper, not a Strategy + Registry: there is exactly
one merge policy in use, and the LLM's `replan` action doesn't accept
parameters. If a second merge mode ever materialises (e.g. preserve
done-only, drop blocked), promote this to an ABC and add a factory."""

from __future__ import annotations

import logging
from typing import Any, Optional

from briar.agent._llm import LLMProvider
from briar.plan._board import BoardReader
from briar.plan._models import ImplementationPlan

log = logging.getLogger(__name__)


def replan(
    old: ImplementationPlan,
    *,
    reader: Optional[BoardReader] = None,
    llm: Optional[LLMProvider] = None,
    max_cards: int = 50,
    context_sections: Optional[Any] = None,
) -> ImplementationPlan:
    """Build a fresh plan from the same board and merge prior statuses
    into it. `reader` defaults to whatever `resolve_board(old.board_url)`
    returns; pass an override for tests."""
    # Local import to avoid a cycle: __init__.build_plan imports models,
    # _replan, etc.
    from briar.plan import build_plan

    fresh = build_plan(
        board_url=old.board_url,
        name=old.name,
        company=old.company,
        default_branch=old.default_branch,
        max_cards=max_cards,
        llm=llm,
        context_sections=context_sections,
        reader=reader,
    )
    prior = {c.key: c for c in old.cards}
    for c in fresh.cards:
        if c.key in prior:
            c.status = prior[c.key].status
            if prior[c.key].last_attempt_summary and not c.last_attempt_summary:
                c.last_attempt_summary = prior[c.key].last_attempt_summary
    log.info(
        "replan: name=%s old_cards=%d new_cards=%d kept_status=%d",
        old.name,
        len(old.cards),
        len(fresh.cards),
        sum(1 for c in fresh.cards if c.key in prior),
    )
    return fresh
