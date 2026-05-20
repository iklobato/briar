"""Command registry — Strategy + Factory composition.

After the API-removal cut, the surface is five commands:
  extract  · runbook  · scaffold  · context  · version
"""

from __future__ import annotations

from typing import Dict, List, Type

from briar.commands.base import Command, confirm
from briar.commands.context import ContextCommand
from briar.commands.dashboard import CommandDashboard
from briar.commands.extract import CommandExtract
from briar.commands.iac import CommandScaffold
from briar.commands.runbook import CommandRunbook
from briar.commands.version import CommandVersion


class CommandRegistry:
    """Resolves the {name → Command} map. Static-only — no instance
    state, no mutation after import time."""

    COMMANDS: List[Type[Command]] = [
        CommandExtract, CommandRunbook, CommandScaffold,
        ContextCommand, CommandDashboard, CommandVersion,
    ]

    @classmethod
    def build(cls) -> Dict[str, Command]:
        return {cls_.name: cls_() for cls_ in cls.COMMANDS}


# Back-compat shim — `build_registry()` was the previous public name.
build_registry = CommandRegistry.build


__all__ = ["Command", "CommandRegistry", "build_registry", "confirm"]
