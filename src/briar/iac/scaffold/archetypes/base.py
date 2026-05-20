"""AgentArchetype contract.

An archetype declares:
- the agent persona (role / goal / backstory) — what shows up in the
  Briar agent's system_prompt;
- a tool-filter that selects which of the SOURCE-contributed tools
  the agent gets bound to (an engineer keeps `commit_files` + `open_pr`,
  a triager doesn't);
- `consumes`: the names of the extractor outputs this archetype should
  consult before acting. Used by the scaffold composer to wire the
  right knowledge-file sections into the agent's prompt, and by the
  dashboard to show which extractor each archetype depends on.

Tool filtering is what makes archetypes more than prompt boilerplate
— a triager that has no `commit_files` tool literally can't open a PR,
regardless of what the LLM "wants" to do."""

from __future__ import annotations

from abc import ABC
from typing import Any, ClassVar, Dict, Iterable, List, Tuple


class AgentArchetype(ABC):
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    role: ClassVar[str] = ""
    goal: ClassVar[str] = ""
    backstory_template: ClassVar[str] = ""
    max_iter: ClassVar[int] = 8

    # Implementation-ref substring whitelist. Empty = all source-tools
    # included. Otherwise: only tools whose `implementation_ref`
    # contains one of these strings get bound.
    tool_filter: ClassVar[Tuple[str, ...]] = ()

    # Extractor names whose output this archetype should consult before
    # taking an action. Order matters — the prompt lists them in this
    # order. Empty = the archetype doesn't depend on extracted knowledge.
    consumes: ClassVar[Tuple[str, ...]] = ()

    def filter_tools(self, tools: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self.tool_filter:
            return list(tools)
        out: List[Dict[str, Any]] = []
        for t in tools:
            ref = t.get("implementation_ref", "")
            for needle in self.tool_filter:
                if needle in ref:
                    out.append(t)
                    break
        return out

    def build_persona(self, target: str) -> Dict[str, str]:
        """`target` is a human-readable string like 'iklobato/lightapi'
        spliced into every persona field that contains `{target}`."""
        ctx = {"target": target}
        return {
            "role": self.role.format(**ctx),
            "goal": self.goal.format(**ctx),
            "backstory": self.backstory_template.format(**ctx),
        }
