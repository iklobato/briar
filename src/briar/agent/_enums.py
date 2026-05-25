"""Closed enumerations for the agent subsystem.

Per ARCHITECTURE_MAP.md §21: enums for closed domain sets, registries
for open plug-in spaces. `StopReason` is the canonical set every LLM
provider translates its vendor-specific stop_reason into; renaming a
value is one edit here instead of grep-replace across 10+ sites.

Uses `str, Enum` multiple inheritance (not `StrEnum`) for 3.10
compatibility — same behaviour, `StopReason.END_TURN == "end_turn"`
remains True so any not-yet-migrated bare-string comparison keeps
working during the rollout.
"""
from __future__ import annotations

from enum import Enum


class StopReason(str, Enum):
    """Canonical reasons an LLM turn ended.

    Each LLM provider adapter (Anthropic, OpenAI, Gemini, Bedrock)
    translates its vendor-specific stop_reason into one of these values
    before populating `LLMResponse.stop_reason`. The runner dispatches
    on these values.
    """

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    DRY_RUN = "dry_run"
    MAX_ITERATIONS = "max_iterations"
    UNEXPECTED = "unexpected"
