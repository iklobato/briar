"""Scaffold templates — Strategy pattern + registry.

Each template is a small class that:
- declares the extra argparse args needed (`--owner`, `--repo`, …)
- builds a complete `ConfigFile`-shaped dict in `build(args)`

Adding a new template = one subclass + one entry in `TEMPLATES`."""

from __future__ import annotations

from typing import Dict

from briar._registry import build_registry
from briar.iac.scaffold.base import ScaffoldTemplate
from briar.iac.scaffold.implementation import ScaffoldImplementation
from briar.iac.scaffold.pr_fixes import ScaffoldPrFixes


TEMPLATES: Dict[str, ScaffoldTemplate] = build_registry(
    (ScaffoldImplementation(), ScaffoldPrFixes()),
    kind="scaffold template",
)


__all__ = ["ScaffoldTemplate", "TEMPLATES"]
