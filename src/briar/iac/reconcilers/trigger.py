"""Trigger reconciler — resolves `workflow_key` to a workflow uuid."""

from __future__ import annotations

from typing import Any, Dict

from briar.errors import ConfigError
from briar.iac.reconcilers.base import ResourceReconciler
from briar.iac.reference_map import ReferenceMap


class ReconcileTrigger(ResourceReconciler):
    kind = "triggers"
    base_path = "/api/v1/triggers/"

    def project(self, spec: Dict[str, Any], refs: ReferenceMap) -> Dict[str, Any]:
        wf_key = spec.get("workflow_key")
        wf_id = spec.get("target_workflow") or (
            refs.lookup("workflows", wf_key) if wf_key else ""
        )
        if not wf_id:
            label = spec.get("key") or spec.get("name")
            raise ConfigError(
                f"triggers.{label}: either `target_workflow` (uuid) or "
                f"`workflow_key` required"
            )
        return {
            "name": spec["name"],
            "kind": spec["kind"],
            "filter_rules": spec.get("filter_rules", {}),
            "payload_to_context_mapping":
                spec.get("payload_to_context_mapping", {}),
            "target_workflow": wf_id,
            "signing_secret_ref": spec.get("signing_secret_ref"),
            "schedule_cron": spec.get("schedule_cron", ""),
            "is_enabled": spec.get("is_enabled", True),
        }
