"""Catalogue resources — thin `CommandResource` subclasses.

These contain no extra logic; everything they need is on the
template base class. Each subclass is essentially a configuration
object (name + base_path + columns + read_only). Adding a new
catalogue endpoint = ~5 lines."""

from __future__ import annotations

from typing import List

from briar.commands.resource import CommandResource


class CommandAgents(CommandResource):
    name = "agents"
    help = "Manage agents."
    base_path = "/api/v1/agents/"
    columns: List[str] = ["id", "name", "model_alias", "kind"]


class CommandTools(CommandResource):
    name = "tools"
    help = "Manage tools."
    base_path = "/api/v1/tools/"
    columns: List[str] = ["id", "name", "kind"]


class CommandSkills(CommandResource):
    name = "skills"
    help = "Manage skills."
    base_path = "/api/v1/skills/"
    columns: List[str] = ["id", "name"]


class CommandSources(CommandResource):
    name = "sources"
    help = "Manage sources."
    base_path = "/api/v1/sources/"
    columns: List[str] = ["id", "name", "kind"]


class CommandTriggers(CommandResource):
    name = "triggers"
    help = "Manage triggers."
    base_path = "/api/v1/triggers/"
    columns: List[str] = ["id", "kind", "workflow", "is_active"]


class CommandSecrets(CommandResource):
    name = "secrets"
    help = "Manage secrets (values are write-only and never returned)."
    base_path = "/api/v1/secrets/"
    columns: List[str] = ["id", "name", "scope", "created_at"]


class CommandBudgets(CommandResource):
    name = "budgets"
    help = "Manage budgets."
    base_path = "/api/v1/budgets/"
    columns: List[str] = ["id", "scope", "amount", "currency", "period"]


class CommandBudgetAlerts(CommandResource):
    name = "budget-alerts"
    help = "Manage budget alerts."
    base_path = "/api/v1/budget-alerts/"
    columns: List[str] = ["id", "budget", "threshold", "channel"]


class CommandAuditEvents(CommandResource):
    name = "audit-events"
    help = "Read audit-events (read-only)."
    base_path = "/api/v1/audit-events/"
    columns: List[str] = ["id", "actor", "action", "created_at"]
    read_only = True


class CommandLlmProviders(CommandResource):
    name = "llm-providers"
    help = "Manage LLM providers."
    base_path = "/api/v1/llm/providers/"
    columns: List[str] = ["id", "name", "kind"]


class CommandLlmModels(CommandResource):
    name = "llm-models"
    help = "Manage LLM models."
    base_path = "/api/v1/llm/models/"
    columns: List[str] = ["id", "alias", "provider", "model_id"]
