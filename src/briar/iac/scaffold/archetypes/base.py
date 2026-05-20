"""AgentArchetype contract.

An archetype is a triple:
- the agent persona (role / goal / backstory)
- a tool-filter that selects which of the SOURCE-contributed tools
  the agent should be bound to (e.g. an engineer gets `commit_files`
  + `open_pr`; a triager doesn't)
- a default max_iter

Tool filtering is what makes archetypes more than just prompt
boilerplate — a triager that has no `commit_files` tool literally
can't open a PR, regardless of what the LLM "wants" to do."""

from __future__ import annotations

from abc import ABC
from typing import Any, ClassVar, Dict, Iterable, List


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
    tool_filter: ClassVar[tuple[str, ...]] = ()

    def filter_tools(
        self,
        tools: Iterable[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
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
        spliced into the backstory template."""
        return {
            "role": self.role,
            "goal": self.goal,
            "backstory": self.backstory_template.format(target=target),
        }
