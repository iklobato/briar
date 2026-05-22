"""Agent archetype registry.

An archetype captures the *role* of an agent — its persona prompts
plus the *filter* that decides which of the available tools it gets
bound to. Adding a new archetype = one subclass + one entry."""

from __future__ import annotations

from typing import Dict

from briar._registry import build_registry
from briar.iac.scaffold.archetypes.base import AgentArchetype
from briar.iac.scaffold.archetypes.engineer import ArchetypeEngineer
from briar.iac.scaffold.archetypes.pr_ci_fixer import ArchetypePrCiFixer
from briar.iac.scaffold.archetypes.pr_conflict_resolver import ArchetypePrConflictResolver
from briar.iac.scaffold.archetypes.pr_fixer import ArchetypePrFixer
from briar.iac.scaffold.archetypes.triager import ArchetypeTriager


ARCHETYPES: Dict[str, AgentArchetype] = build_registry(
    (ArchetypeEngineer(), ArchetypePrCiFixer(), ArchetypePrConflictResolver(), ArchetypePrFixer(), ArchetypeTriager()),
    kind="agent archetype",
)


__all__ = ["AgentArchetype", "ARCHETYPES"]
