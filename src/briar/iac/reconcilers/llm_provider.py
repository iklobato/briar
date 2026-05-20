"""LLMProvider reconciler — admin-managed resources are pre-indexed
by *both* `name` and `kind` so configs can refer to either."""

from __future__ import annotations

from typing import Any, Dict

from briar.http import ApiClient
from briar.iac.reconcilers.base import ResourceReconciler
from briar.iac.reference_map import ReferenceMap


class ReconcileLlmProvider(ResourceReconciler):
    kind = "llm_providers"
    base_path = "/api/v1/llm/providers/"
    name_field = "name"

    def project(self, spec: Dict[str, Any], refs: ReferenceMap) -> Dict[str, Any]:
        return {
            "name": spec["name"],
            "kind": spec.get("kind") or spec["name"],
            "api_base": spec.get("api_base", ""),
            "config": spec.get("config", {}),
            "is_enabled": spec.get("is_enabled", True),
        }

    def index_existing(self, client: ApiClient, refs: ReferenceMap) -> None:
        for it in client.list_all(self.base_path):
            if type(it) is not dict:
                continue
            uuid = it.get("id")
            if not uuid:
                continue
            for key_field in ("name", "kind"):
                value = it.get(key_field)
                if value:
                    refs.remember(self.kind, str(value), str(uuid))
