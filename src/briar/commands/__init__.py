"""Command registry — Strategy + Factory composition.

`Command` is the contract (re-exported from `.base`); `build_registry()`
assembles a dict of `{name: Command()}` from the concrete classes in
sibling modules. Adding a new top-level command is one class + one
entry in `_COMMAND_CLASSES`.
"""

from __future__ import annotations

from typing import Dict, List, Type

from briar.commands.auth import (
    CommandLogin, CommandLogout, CommandRegister, CommandWhoami,
)
from briar.commands.base import Command, confirm
from briar.commands.catalogue import (
    CommandAgents,
    CommandAuditEvents,
    CommandBudgetAlerts,
    CommandBudgets,
    CommandLlmModels,
    CommandLlmProviders,
    CommandSecrets,
    CommandSkills,
    CommandSources,
    CommandTools,
    CommandTriggers,
)
from briar.commands.checkpoints import CommandCheckpoints
from briar.commands.config_cmd import CommandConfig
from briar.commands.context import ContextCommand
from briar.commands.extract import CommandExtract
from briar.commands.iac import (
    CommandApply, CommandDestroy, CommandExport, CommandPlan, CommandScaffold,
)
from briar.commands.memberships import CommandMemberships
from briar.commands.oauth import CommandOauth
from briar.commands.profile_cmd import CommandProfile
from briar.commands.raw_api import CommandApi
from briar.commands.runbook import CommandRunbook
from briar.commands.runs import CommandRuns
from briar.commands.tasks import CommandTasks
from briar.commands.version import CommandVersion
from briar.commands.workflows import CommandWorkflows, CommandWorkflowTemplates
from briar.commands.workspaces import CommandWorkspace


_COMMAND_CLASSES: List[Type[Command]] = [
    CommandLogin, CommandLogout, CommandRegister, CommandWhoami,
    CommandWorkspace, CommandMemberships,
    CommandAgents, CommandTools, CommandSkills, CommandSources,
    CommandWorkflows, CommandWorkflowTemplates,
    CommandTriggers, CommandTasks, CommandRuns,
    CommandLlmProviders, CommandLlmModels, CommandSecrets,
    CommandBudgets, CommandBudgetAlerts, CommandAuditEvents,
    CommandCheckpoints, CommandOauth,
    CommandApply, CommandPlan, CommandDestroy, CommandScaffold, CommandExport,
    CommandRunbook, CommandExtract, ContextCommand,
    CommandApi, CommandConfig, CommandProfile, CommandVersion,
]


def build_registry() -> Dict[str, Command]:
    return {cls.name: cls() for cls in _COMMAND_CLASSES}


__all__ = ["Command", "build_registry", "confirm"]
