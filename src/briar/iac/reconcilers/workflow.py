"""Workflow reconciler — graph projection (agent_key → agent_id)."""

from __future__ import annotations

from typing import Any, Dict, List

from briar.errors import ConfigError
from briar.iac.reconcilers.base import ResourceReconciler
from briar.iac.reference_map import ReferenceMap


def _project_graph(graph: Dict[str, Any], refs: ReferenceMap) -> Dict[str, Any]:
    """Substitute every `agent_key` / `parallel_agent_keys` reference in
    the node list with the resolved uuid form expected by the server."""
    nodes_out: List[Dict[str, Any]] = []
    for node in graph.get("nodes") or []:
        if type(node) is not dict:
            continue
        new_node = dict(node)
        agent_key = new_node.pop("agent_key", None)
        if agent_key:
            new_node["agent_id"] = refs.lookup("agents", agent_key)
        parallel_keys = new_node.pop("parallel_agent_keys", None)
        if parallel_keys:
            new_node["parallel_agent_ids"] = [
                refs.lookup("agents", k) for k in parallel_keys
            ]
        nodes_out.append(new_node)
    return {
        "process": graph.get("process", "sequential"),
        "entry": graph["entry"],
        "nodes": nodes_out,
    }


class ReconcileWorkflow(ResourceReconciler):
    kind = "workflows"
    base_path = "/api/v1/workflows/"

    def project(self, spec: Dict[str, Any], refs: ReferenceMap) -> Dict[str, Any]:
        graph = spec.get("graph")
        if type(graph) is not dict:
            label = spec.get("key") or spec.get("name")
            raise ConfigError(
                f"workflows.{label}: `graph` must be an object"
            )
        return {
            "name": spec["name"],
            "description": spec.get("description", ""),
            "graph": _project_graph(graph, refs),
            "auto_merge_rules": spec.get("auto_merge_rules", {}),
        }
