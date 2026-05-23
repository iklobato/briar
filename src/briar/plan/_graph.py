"""Dependency-graph helpers for `briar plan`.

The board reader hands us a list of `PlanCard`s with `depends_on` set
from explicit tracker links and/or LLM synthesis. The graph helpers
here turn that list into an ordered implementation queue:

* `topological_sort` — Kahn's algorithm, deterministic, raises on a
  cycle. The output list is in dependency order: every card in the
  result appears after all of its `depends_on` ancestors.
* `apply_cascade` — walks the sorted list and sets each card's
  `branch_parent` to the branch name of the latest upstream card in
  cascade mode, or to `default_branch` otherwise.

Unknown `depends_on` keys (deps that point to tickets outside the
current board) are silently dropped — they can't be sequenced and
shouldn't block the rest of the plan."""

from __future__ import annotations

from typing import Dict, List, Set

from briar.errors import CliError
from briar.plan._models import PlanCard


def topological_sort(cards: List[PlanCard]) -> List[PlanCard]:
    """Stable topological order. Cards with no deps come first in
    their input order; cards with deps come after all their upstreams."""
    by_key: Dict[str, PlanCard] = {c.key: c for c in cards}
    # Trim deps to keys we actually have; out-of-board deps cannot be
    # ordered and shouldn't poison the graph.
    indeg: Dict[str, int] = {}
    deps: Dict[str, List[str]] = {}
    rev: Dict[str, List[str]] = {k: [] for k in by_key}
    for card in cards:
        valid = [d for d in card.depends_on if d in by_key and d != card.key]
        deps[card.key] = valid
        indeg[card.key] = len(valid)
        for d in valid:
            rev[d].append(card.key)

    # Kahn's algorithm — process by input order so output is stable.
    queue: List[str] = [c.key for c in cards if indeg[c.key] == 0]
    out: List[PlanCard] = []
    seen: Set[str] = set()
    while queue:
        key = queue.pop(0)
        if key in seen:
            continue
        seen.add(key)
        out.append(by_key[key])
        for downstream in rev[key]:
            indeg[downstream] -= 1
            if indeg[downstream] == 0:
                queue.append(downstream)

    if len(out) != len(cards):
        unresolved = [c.key for c in cards if c.key not in seen]
        raise CliError(
            f"dependency cycle detected — cards still un-orderable after Kahn: {unresolved}"
        )

    # Persist the trimmed dep list back to each card so downstream
    # consumers don't have to repeat the filter.
    for card in out:
        card.depends_on = deps[card.key]
    return out


def apply_cascade(
    cards: List[PlanCard],
    *,
    cascade: bool,
    default_branch: str,
) -> List[PlanCard]:
    """Assign each card a `branch_parent`. Without cascade, every card
    branches from `default_branch`. With cascade, a card's parent is
    the branch of its latest dependency in the sorted order (the most
    recently merged ancestor at implementation time)."""
    order = {c.key: i for i, c in enumerate(cards)}
    for card in cards:
        if not card.branch_name:
            card.branch_name = _suggest_branch(card.key)
        if not cascade or not card.depends_on:
            card.branch_parent = default_branch
            continue
        # Pick the dep whose own index in the sort is highest — that's
        # the one whose branch will hold the most accumulated context
        # when this card starts.
        latest = max(card.depends_on, key=lambda d: order.get(d, -1))
        parent_card = next((c for c in cards if c.key == latest), None)
        card.branch_parent = parent_card.branch_name if parent_card else default_branch
    return cards


def _suggest_branch(key: str) -> str:
    """`KAN-12` → `briar/kan-12`; `#42` → `briar/issue-42`."""
    slug = key.strip().lower().replace("#", "issue-").replace(" ", "-").replace("/", "-")
    slug = "".join(c for c in slug if c.isalnum() or c in "-_")
    return f"briar/{slug or 'card'}"
