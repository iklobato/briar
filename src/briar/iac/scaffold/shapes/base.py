"""WorkflowShape contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict


class WorkflowShape(ABC):
    """Builds the `workflow.graph` dict for a given agent key."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    @abstractmethod
    def build_graph(self, agent_key: str) -> Dict[str, Any]:
        """Emit the graph dict consumed by the orchestrator."""
