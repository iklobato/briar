"""WorkflowShape contract."""

from __future__ import annotations

from typing import Any, ClassVar, Dict


class WorkflowShape:
    """Builds the `workflow.graph` dict for a given agent key.

    The graph references a single agent key — composing multiple agents
    is the orchestrator's job via `parallel` / `subworkflow` nodes,
    which are themselves separate shapes."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def build_graph(self, agent_key: str) -> Dict[str, Any]:
        raise NotImplementedError
