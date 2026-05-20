"""One-shot agent run with no checkpoint and no branching.

Suitable for jobs that should "just happen" on a schedule — the
PR-fix cron is the canonical example. The hourly trigger fires, the
agent sweeps all unresolved review comments, applies fixes, and
exits. No human in the loop."""

from __future__ import annotations

from typing import Any, Dict

from briar.iac.scaffold.shapes.base import WorkflowShape


class ShapeOneShot(WorkflowShape):
    name = "one-shot"
    description = "single agent node, no checkpoint, no branching"

    def build_graph(self, agent_key: str) -> Dict[str, Any]:
        return {
            "process": "sequential",
            "entry": "run",
            "nodes": [
                {
                    "id": "run",
                    "kind": "agent",
                    "agent_key": agent_key,
                    "prompt": ("Read the gathered context and take the appropriate " "actions using your bound tools."),
                    "next": None,
                },
            ],
        }
