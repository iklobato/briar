"""Agent archetype registry.

An archetype captures the *role* of an agent — its persona prompts
plus the *filter* that decides which of the available tools it gets
bound to. Adding a new archetype = one subclass + one entry."""

from __future__ import annotations

from typing import Dict

from briar.iac.scaffold.archetypes.base import AgentArchetype
from briar.iac.scaffold.archetypes.engineer import ArchetypeEngineer
from briar.iac.scaffold.archetypes.pr_fixer import ArchetypePrFixer
from briar.iac.scaffold.archetypes.triager import ArchetypeTriager


ARCHETYPES: Dict[str, AgentArchetype] = {
    a.name: a for a in (
        ArchetypeEngineer(),
        ArchetypePrFixer(),
        ArchetypeTriager(),
    )
}


__all__ = ["AgentArchetype", "ARCHETYPES"]
