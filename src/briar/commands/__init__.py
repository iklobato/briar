"""Command registry — Strategy + Factory composition.

Top-level commands: agent · auth · chat · context · dashboard · extract ·
journal · mcp · plan · runbook · scaffold · secrets · telemetry · version.
"""

from __future__ import annotations

from typing import Dict, List, Type

from briar.commands.agent import CommandAgent
from briar.commands.auth import CommandAuth
from briar.commands.base import Command, confirm
from briar.commands.chat import CommandChat
from briar.commands.context import ContextCommand
from briar.commands.dashboard import CommandDashboard
from briar.commands.extract import CommandExtract
from briar.commands.iac import CommandScaffold
from briar.commands.journal import CommandJournal
from briar.commands.mcp import CommandMcp
from briar.commands.plan import CommandPlan
from briar.commands.runbook import CommandRunbook
from briar.commands.secrets import CommandSecrets
from briar.commands.telemetry import CommandTelemetry
from briar.commands.version import CommandVersion


class CommandRegistry:
    """Resolves the {name → Command} map. Static-only — no instance
    state, no mutation after import time."""

    COMMANDS: List[Type[Command]] = [
        CommandExtract,
        CommandRunbook,
        CommandScaffold,
        ContextCommand,
        CommandDashboard,
        CommandAgent,
        CommandAuth,
        CommandPlan,
        CommandSecrets,
        CommandJournal,
        CommandMcp,
        CommandChat,
        CommandTelemetry,
        CommandVersion,
    ]

    @classmethod
    def build(cls) -> Dict[str, Command]:
        return {cls_.name: cls_() for cls_ in cls.COMMANDS}


__all__ = ["Command", "CommandRegistry", "confirm"]
