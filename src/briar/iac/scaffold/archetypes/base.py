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
from typing import Any, ClassVar, Dict, Iterable, List, Literal, Tuple


# Closed set of `implementation_ref` substrings recognised by archetypes.
# Typos in subclass `tool_filter = (...)` assignments become type-checker
# errors instead of silently dropping the tool. Add a new needle here +
# in the matching source's `build_tools` output to extend.
ToolFilterNeedle = Literal[
    "comment_on_issue",
    "add_labels",
    "comment",
    "commit",
    "open_pr",
]


class AgentArchetype(ABC):
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    role: ClassVar[str] = ""
    goal: ClassVar[str] = ""
    backstory_template: ClassVar[str] = ""
    max_iter: ClassVar[int] = 8

    # Implementation-ref substring whitelist. Empty = all source-tools
    # included. Otherwise: only tools whose `implementation_ref`
    # contains one of these needles get bound.
    tool_filter: ClassVar[Tuple[ToolFilterNeedle, ...]] = ()

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
        """`target` is a human-readable string like 'acme/widgets'
        spliced into every persona field that contains `{target}`.

        The backstory is composed in two parts:
        1. The archetype's own `backstory_template` (persona-specific
           procedures + tone).
        2. Every rule in the global registry whose `applies_to` includes
           this archetype, rendered with severity-ordered headings.

        The second part is shared across archetypes — adding a new rule
        that hits `pr-fixer` AND `pr-conflict-resolver` is one markdown
        file, no archetype edits."""
        from briar.iac.scaffold.rules import RuleRegistry

        ctx = {"target": target}
        backstory = self.backstory_template.format(**ctx)
        rules = RuleRegistry.for_archetype(self.name)
        if rules:
            rule_chunks: List[str] = ["", "## Inherited rules", ""]
            for rule in rules:
                rule_chunks.append(f"### [{rule.severity}] {rule.name}")
                rule_chunks.append("")
                rule_chunks.append(rule.render())
                rule_chunks.append("")
            backstory = backstory.rstrip() + "\n\n" + "\n".join(rule_chunks).strip() + "\n"
        return {
            "role": self.role.format(**ctx),
            "goal": self.goal.format(**ctx),
            "backstory": backstory,
        }
