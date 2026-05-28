"""IaC: scaffold templates + config-file loader."""

from __future__ import annotations

from briar.iac.config_file import ConfigFile
from briar.iac.scaffold import TEMPLATES, ScaffoldTemplate

__all__ = [
    "ConfigFile",
    "TEMPLATES",
    "ScaffoldTemplate",
]
