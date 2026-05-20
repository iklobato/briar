"""One-shot agent run with no checkpoint and no branching.

Suitable for jobs that should "just happen" on a schedule — the
PR-fix cron is the canonical example. The trigger fires, the agent
sweeps unresolved review comments, applies fixes, and exits. No human
in the loop."""

from __future__ import annotations

from typing import Any, Dict

from briar.iac.scaffold.shapes.base import WorkflowShape


_RUN_PROMPT = (
    "Execute your archetype's directive in ONE pass. Procedure:\n"
    "\n"
    "1. **Read first**. Walk the consumed knowledge sections listed in "
    "your archetype, in the order they're declared. Stop reading only "
    "after you've extracted the per-section signal the archetype "
    "requires.\n"
    "2. **Act**. Use ONLY the tools bound to your archetype. Each tool "
    "call should be justified by something you read in step 1; if you "
    "can't cite a section, don't make the call.\n"
    "3. **Summarise**. End your output with a `## Actions` block "
    "listing every tool you invoked, each line: "
    "`<tool> — <one-line rationale citing a section>`. If you took no "
    "actions because the knowledge said nothing needed doing, say so "
    "and explain.\n"
    "\n"
    "Do not act on things outside your archetype's scope. If you notice "
    "an issue your archetype isn't allowed to fix, mention it in the "
    "summary so a human can decide."
)


class ShapeOneShot(WorkflowShape):
    name = "one-shot"
    description = "single agent node, no checkpoint, no branching"

    def build_graph(self, agent_key: str) -> Dict[str, Any]:
        return {
            "process": "sequential",
            "entry": "run",
            "nodes": [
                {"id": "run", "kind": "agent", "agent_key": agent_key, "prompt": _RUN_PROMPT, "next": ""},
            ],
        }
