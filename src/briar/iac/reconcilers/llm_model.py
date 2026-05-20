"""LLMModel reconciler — projects `provider_key` to a provider uuid."""

from __future__ import annotations

from typing import Any, Dict

from briar.errors import ConfigError
from briar.iac.reconcilers.base import ResourceReconciler
from briar.iac.reference_map import ReferenceMap


_OPTIONAL_KEYS = (
    "price_per_1k_input_usd",
    "price_per_1k_output_usd",
    "pricing_strategy",
    "pricing_config",
)


class ReconcileLlmModel(ResourceReconciler):
    kind = "llm_models"
    base_path = "/api/v1/llm/models/"

    def project(self, spec: Dict[str, Any], refs: ReferenceMap) -> Dict[str, Any]:
        provider_key = spec.get("provider_key")
        provider_id = spec.get("provider") or (
            refs.lookup("llm_providers", provider_key) if provider_key else ""
        )
        if not provider_id:
            label = spec.get("key") or spec.get("name")
            raise ConfigError(
                f"llm_models.{label}: either `provider` (uuid) or "
                f"`provider_key` (config key) required"
            )
        body: Dict[str, Any] = {
            "name": spec["name"],
            "provider": provider_id,
            "display_name": spec.get("display_name") or spec["name"],
            "default_params": spec.get("default_params", {}),
            "credential_binding": spec.get("credential_binding"),
            "is_enabled": spec.get("is_enabled", True),
        }
        # Pricing fields are optional on the spec but the server rejects
        # nulls. Only include them when the user actually set a value.
        for opt in _OPTIONAL_KEYS:
            value = spec.get(opt)
            if value is not None:
                body[opt] = value
        return body
