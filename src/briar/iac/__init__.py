"""Infrastructure-as-code: declarative reconcilers + scaffolding.

Public surface:
- `ConfigFile`     — parsed bundle from a JSON config
- `reconcile()`    — plan or apply
- `destroy_all()`  — reverse-dependency teardown
- `TEMPLATES`      — registry of scaffold templates
"""

from __future__ import annotations

from briar.iac.config_file import ConfigFile
from briar.iac.engine import destroy_all, reconcile
from briar.iac.reference_map import ReferenceMap
from briar.iac.scaffold import TEMPLATES, ScaffoldTemplate

__all__ = [
    "ConfigFile",
    "ReferenceMap",
    "TEMPLATES",
    "ScaffoldTemplate",
    "destroy_all",
    "reconcile",
]
