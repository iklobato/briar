"""Runbook YAML schema (Pydantic).

A runbook describes "what to apply where" for N companies × N
templates in a single file. Each `RunbookEntry` resolves to one
`briar scaffold … | briar apply -` invocation; the executor walks the
whole file in one go.

Sources and triggers are discriminated unions on `kind`, mirroring the
scaffold's pluggable registries — adding a new tracker / cloud source
or trigger flavour means adding one entry to the existing source /
trigger registry **and** one Union variant here.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """All schema models forbid unknown keys — typos surface at load
    time with a locator-aware Pydantic error, never silently."""
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Source variants
# ---------------------------------------------------------------------------

class GithubSourceEntry(_Strict):
    kind: Literal["github"]
    # Optional overrides — fall back to the company defaults.
    auth_mode: Optional[Literal["oauth", "pat"]] = None
    github_secret_id: Optional[str] = None
    # User filters — restrict which issues the agent sees. Both
    # composition: allow ∩ ¬block (when both set).
    authors_allow: List[str] = Field(default_factory=list)
    authors_block: List[str] = Field(default_factory=list)
    assignees_allow: List[str] = Field(default_factory=list)
    assignees_block: List[str] = Field(default_factory=list)


class JiraSourceEntry(_Strict):
    kind: Literal["jira"]
    project: List[str] = Field(default_factory=list)
    jql: Optional[str] = None
    secret_id: Optional[str] = None
    # Same user filters as GitHub. Backend Jira connector folds these
    # into the JQL it issues; identifiers are accountIds or emails.
    authors_allow: List[str] = Field(default_factory=list)
    authors_block: List[str] = Field(default_factory=list)
    assignees_allow: List[str] = Field(default_factory=list)
    assignees_block: List[str] = Field(default_factory=list)


class AwsSourceEntry(_Strict):
    kind: Literal["aws"]
    role_arn: Optional[str] = None
    external_id: Optional[str] = None
    region: str = "us-east-1"
    services: List[str] = Field(default_factory=list)


SourceEntry = Annotated[
    Union[GithubSourceEntry, JiraSourceEntry, AwsSourceEntry],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Trigger variants
# ---------------------------------------------------------------------------

class WebhookTriggerEntry(_Strict):
    kind: Literal["github_webhook"]
    events: List[str] = Field(default_factory=list)
    labels: List[str] = Field(default_factory=lambda: ["briar"])


class CronTriggerEntry(_Strict):
    kind: Literal["schedule_cron"]
    schedule: str = "0 * * * *"


class ManualTriggerEntry(_Strict):
    kind: Literal["manual"]


TriggerEntry = Annotated[
    Union[WebhookTriggerEntry, CronTriggerEntry, ManualTriggerEntry],
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Runbook + Company + Top-level
# ---------------------------------------------------------------------------

class RunbookEntry(_Strict):
    """One scaffold + apply invocation."""

    template: Literal["implementation", "pr-fixes"]
    prefix: str
    owner: str
    repo: str
    sources: List[SourceEntry] = Field(min_length=1)
    trigger: TriggerEntry

    # Optional overrides — fall back to company defaults or scaffold-default
    archetype: Optional[str] = None
    shape: Optional[str] = None
    llm_provider_key: Optional[str] = None
    model: Optional[str] = None


class CompanyDefaults(_Strict):
    """Per-company shared values inherited into each runbook in that company.

    Anything not set here falls back to the scaffold's own defaults
    (e.g. `claude-sonnet-4-6` for `model`, `engineer` for
    `archetype`)."""

    llm_provider_key: Optional[str] = None
    model: Optional[str] = None
    auth_mode: Optional[Literal["oauth", "pat"]] = None
    github_secret_id: Optional[str] = None
    archetype: Optional[str] = None
    shape: Optional[str] = None


class ExtractEntry(_Strict):
    """Per-company extractor selection with its kind-specific args.

    Each `name` maps to one entry in `EXTRACTORS`. `args` is a free-form
    dict whose keys mirror the extractor's CLI flags (`pr_repo`,
    `aws_extract_profile`, …). Unknown keys are silently ignored,
    matching argparse semantics."""

    name: Literal[
        "pr-archaeology",
        "aws-infra",
        "active-work",
        "github-deployments",
        "codebase-conventions",
    ]
    args: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeBinding(_Strict):
    """Where this company's knowledge blob lives + how the runbook
    consumes it at apply time.

    `store` picks the backend (one of `KNOWLEDGE_STORE_NAMES`); `name`
    is the blob's logical name (used directly by `briar-api`, mapped to
    a path by `file`). `mode` controls how the executor applies it:
      - `inject` (default) — prepend the blob's content to every agent's
        `system_prompt` at apply time. Works for any store.
      - `bind`             — wire the blob in as a Briar Source and
        add it to every agent's `source_keys`. Only valid for
        `store: briar-api`; the orchestrator gathers it into
        `task.context[source_<name>]` on every run."""

    store: Literal["file", "briar-api"] = "file"
    name: str
    mode: Literal["inject", "bind"] = "inject"
    root: Optional[str] = None  # only meaningful when `store == "file"`


class CompanyEntry(_Strict):
    profile: str
    workspace_id: Optional[str] = None
    api_base: Optional[str] = None
    defaults: Optional[CompanyDefaults] = None

    # Two ways to declare the knowledge blob:
    #
    # 1. `knowledge_file: ./knowledge/acme.md`
    #    — legacy shortcut equivalent to
    #      {store: file, name: <the path>, mode: inject}.
    #
    # 2. `knowledge: {store: ..., name: ..., mode: ...}`
    #    — the full form. Picks the store, mode, etc. explicitly.
    #
    # If both are set the explicit `knowledge:` block wins.
    knowledge_file: Optional[str] = None
    knowledge: Optional[KnowledgeBinding] = None

    extract: List[ExtractEntry] = Field(default_factory=list)

    runbooks: List[RunbookEntry] = Field(min_length=1)


class RunbookFile(_Strict):
    version: int = 1
    companies: Dict[str, CompanyEntry] = Field(min_length=1)
