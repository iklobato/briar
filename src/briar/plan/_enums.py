"""Closed enumerations for the plan subsystem.

Per ARCHITECTURE_MAP.md §21: enums for closed domain sets, registries
for open plug-in spaces. Plan card lifecycle states are intrinsically
a fixed set; previously documented as a comment in `_models.py:39` and
dispatched on by string equality. As an enum, typos fail loud at the
boundary (PlanCardStatus("In_Progress") raises) and renames are one
edit.

`SelectorActionKind` is the matching enum for the LLM picker's return
value — what the selector tells the runner to do next. Same shape:
closed lifecycle, exhaustive match in the runner, no open registry.
"""

from __future__ import annotations

from enum import Enum


class PlanCardStatus(str, Enum):
    """Lifecycle states for one card in an ImplementationPlan."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"


class SelectorActionKind(str, Enum):
    """What the LLM selector decided to do this iteration.

    `PICK` carries a card key; `REPLAN` says "the world has drifted, re-derive
    the card list"; `COMPLETE` means the plan is finished; `BLOCKED` means no
    forward progress is possible without operator intervention. The runner
    exhaustively matches on this enum — adding a fifth action is a deliberate
    schema bump, not a silent registry insertion."""

    PICK = "pick"
    REPLAN = "replan"
    COMPLETE = "complete"
    BLOCKED = "blocked"
