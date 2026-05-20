"""Triage — read sources, label and comment, do not implement.

Same graph shape as one-shot, but the agent archetype that pairs with
it should only bind read + comment tools, never the action ones."""

from __future__ import annotations

from typing import Any, Dict

from briar.iac.scaffold.shapes.base import WorkflowShape


_TRIAGE_PROMPT = (
    "Triage each item from the gathered sources. Procedure per item:\n"
    "\n"
    "1. **Read** the consumed knowledge sections in your archetype's "
    "order. You will use `codebase-conventions` for the area label, "
    "`github-deployments` for severity (look for live incidents), "
    "`pr-archaeology` for the reviewer hint, and `active-work` for "
    "duplicate-PR detection.\n"
    "2. **Label**. Apply ONE area label (derived from the modules the "
    "item references) and ONE severity label (`crit` / `bug` / "
    "`enhancement`). Use `add_labels`. Do not invent labels that "
    "aren't already justifiable from the knowledge.\n"
    "3. **Comment**. Post ONE comment per item via "
    "`comment_on_issue` containing: (a) one-sentence restatement of "
    "the ticket, (b) labels applied + the section that justified each, "
    "(c) likely reviewer from `pr-archaeology`, (d) a duplicate signal "
    "(link to an open PR) if `active-work` shows one.\n"
    "4. **Stop**. Do not commit, do not open PRs, do not transition "
    "status. Triager's output is labels + comment, nothing more.\n"
    "\n"
    "End with a `## Triaged` block listing each item handled, one line "
    "per item: `<item-ref> — <labels-applied>`."
)


class ShapeTriage(WorkflowShape):
    name = "triage"
    description = "single agent that classifies + comments, no write actions"

    def build_graph(self, agent_key: str) -> Dict[str, Any]:
        return {
            "process": "sequential",
            "entry": "triage",
            "nodes": [
                {"id": "triage", "kind": "agent", "agent_key": agent_key, "prompt": _TRIAGE_PROMPT, "next": ""},
            ],
        }
