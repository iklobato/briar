"""IaC: scaffold + config-file loader (post-API removal).

Reconcilers and the reconcile/destroy engine were removed when the
CLI dropped its API surface. ConfigFile parsing stays — useful for
templating and inspection."""

from __future__ import annotations

from briar.iac.config_file import ConfigFile
from briar.iac.reference_map import ReferenceMap
from briar.iac.scaffold import TEMPLATES, ScaffoldTemplate

__all__ = [
    "ConfigFile",
    "ReferenceMap",
    "TEMPLATES",
    "ScaffoldTemplate",
]
