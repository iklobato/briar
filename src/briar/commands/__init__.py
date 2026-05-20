"""Command registry — Strategy + Factory composition.

After the API-removal cut, the surface is five commands:
  extract  · runbook  · scaffold  · context  · version
"""

from __future__ import annotations

from typing import Dict, List, Type

from briar.commands.base import Command, confirm
from briar.commands.context import ContextCommand
from briar.commands.extract import CommandExtract
from briar.commands.iac import CommandScaffold
from briar.commands.runbook import CommandRunbook
from briar.commands.version import CommandVersion


_COMMAND_CLASSES: List[Type[Command]] = [
    CommandExtract, CommandRunbook, CommandScaffold,
    ContextCommand, CommandVersion,
]


def build_registry() -> Dict[str, Command]:
    return {cls.name: cls() for cls in _COMMAND_CLASSES}


__all__ = ["Command", "build_registry", "confirm"]
