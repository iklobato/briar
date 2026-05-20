"""Tool reconciler."""

from __future__ import annotations

from typing import Any, Dict

from briar.iac.reconcilers.base import ResourceReconciler
from briar.iac.reference_map import ReferenceMap


class ReconcileTool(ResourceReconciler):
    kind = "tools"
    base_path = "/api/v1/tools/"

    def project(self, spec: Dict[str, Any], refs: ReferenceMap) -> Dict[str, Any]:
        return {
            "name": spec["name"],
            "description": spec.get("description", ""),
            "input_schema": spec.get("input_schema", {}),
            "output_schema": spec.get("output_schema", {}),
            # Server enum: `read` | `mutate`. Defaults to read-only so
            # accidental misconfig can't escalate to a write tool.
            "side_effect": spec.get("side_effect", "read"),
            "implementation_ref": spec.get("implementation_ref", ""),
            "credentials_ref": spec.get("credentials_ref"),
            "credential_binding": spec.get("credential_binding"),
        }
