"""Triage — read sources, label and comment, do not implement.

Same graph shape as one-shot, but the agent archetype that pairs with
it should only bind the read + comment tools, never the action ones."""

from __future__ import annotations

from typing import Any, Dict

from briar.iac.scaffold.shapes.base import WorkflowShape


class ShapeTriage(WorkflowShape):
    name = "triage"
    description = "single agent that classifies + comments, no write actions"

    def build_graph(self, agent_key: str) -> Dict[str, Any]:
        return {
            "process": "sequential",
            "entry": "triage",
            "nodes": [
                {
                    "id": "triage",
                    "kind": "agent",
                    "agent_key": agent_key,
                    "prompt": (
                        "Categorize each item from the gathered sources. "
                        "Add labels and/or post one comment per item via "
                        "the bound comment tools. Do not commit or open PRs."
                    ),
                    "next": None,
                },
            ],
        }
