"""Agent reconciler — resolves llm_model_key + tool_keys + source_keys."""

from __future__ import annotations

from typing import Any, Dict

from briar.errors import ConfigError
from briar.iac.reconcilers._helpers import read_text_field
from briar.iac.reconcilers.base import ResourceReconciler
from briar.iac.reference_map import ReferenceMap


class ReconcileAgent(ResourceReconciler):
    kind = "agents"
    base_path = "/api/v1/agents/"

    def project(self, spec: Dict[str, Any], refs: ReferenceMap) -> Dict[str, Any]:
        llm_id = self._resolve_llm(spec, refs)
        tool_ids = [
            refs.lookup("tools", k) for k in (spec.get("tool_keys") or [])
        ] + list(spec.get("tool_ids") or [])
        source_ids = [
            refs.lookup("sources", k) for k in (spec.get("source_keys") or [])
        ] + list(spec.get("source_ids") or [])

        body: Dict[str, Any] = {
            "name": spec["name"],
            "role": spec.get("role", ""),
            "goal": spec.get("goal", ""),
            "backstory": spec.get("backstory", ""),
            "system_prompt":
                read_text_field(spec, "system_prompt", "system_prompt_file") or "",
            "llm_model": llm_id,
            "max_iter": spec.get("max_iter", 8),
            "allow_delegation": spec.get("allow_delegation", False),
            "runtime": spec.get("runtime", "crew"),
            "runtime_config": spec.get("runtime_config", {}),
        }
        if tool_ids:
            body["tool_ids"] = tool_ids
        if source_ids:
            body["source_ids"] = source_ids
        fallback_key = spec.get("fallback_llm_model_key")
        if fallback_key:
            body["fallback_llm_model"] = refs.lookup("llm_models", fallback_key)
        return body

    @staticmethod
    def _resolve_llm(spec: Dict[str, Any], refs: ReferenceMap) -> str:
        llm_key = spec.get("llm_model_key")
        llm_id = spec.get("llm_model") or (
            refs.lookup("llm_models", llm_key) if llm_key else ""
        )
        if llm_id:
            return llm_id
        label = spec.get("key") or spec.get("name")
        raise ConfigError(
            f"agents.{label}: either `llm_model` (uuid) or "
            f"`llm_model_key` required"
        )
