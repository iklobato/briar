"""Closed enumerations for the plan subsystem.

Per ARCHITECTURE_MAP.md §21: enums for closed domain sets, registries
for open plug-in spaces. Plan card lifecycle states are intrinsically
a fixed set; previously documented as a comment in `_models.py:39` and
dispatched on by string equality at lines 120, 122. As an enum, typos
fail loud at the boundary (PlanCardStatus("In_Progress") raises) and
renames are one edit.
"""
from __future__ import annotations

from enum import Enum


class PlanCardStatus(str, Enum):
    """Lifecycle states for one card in an ImplementationPlan."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
