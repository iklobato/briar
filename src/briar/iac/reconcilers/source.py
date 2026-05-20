"""Source reconciler."""

from __future__ import annotations

from typing import Any, Dict

from briar.iac.reconcilers.base import ResourceReconciler
from briar.iac.reference_map import ReferenceMap


class ReconcileSource(ResourceReconciler):
    kind = "sources"
    base_path = "/api/v1/sources/"

    def project(self, spec: Dict[str, Any], refs: ReferenceMap) -> Dict[str, Any]:
        return {
            "name": spec["name"],
            "kind": spec["kind"],
            "config": spec.get("config", {}),
            "credentials_ref": spec.get("credentials_ref"),
            "credential_binding": spec.get("credential_binding"),
            # `cache_policy` is a JSONField server-side; the orchestrator
            # calls `.get(...)` on it. Default to an empty dict.
            "cache_policy": spec.get("cache_policy") or {},
            "is_enabled": spec.get("is_enabled", True),
        }
