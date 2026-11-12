"""Command registry — Strategy + Factory composition.

Top-level commands: auth · config · doctor · extract · init · runbook.
"""

from __future__ import annotations

from typing import Dict, List, Type

from briar.commands.auth import CommandAuth
from briar.commands.base import Command, confirm
from briar.commands.config import CommandConfig
from briar.commands.doctor import CommandDoctor
from briar.commands.extract import CommandExtract
from briar.commands.init import CommandInit
from briar.commands.runbook import CommandRunbook


class CommandRegistry:
    """Resolves the {name → Command} map. Static-only — no instance
    state, no mutation after import time."""

    COMMANDS: List[Type[Command]] = [
        CommandExtract,
        CommandRunbook,
        CommandAuth,
        CommandInit,
        CommandConfig,
        CommandDoctor,
    ]

    @classmethod
    def build(cls) -> Dict[str, Command]:
        return {cls_.name: cls_() for cls_ in cls.COMMANDS}


__all__ = ["Command", "CommandRegistry", "confirm"]
