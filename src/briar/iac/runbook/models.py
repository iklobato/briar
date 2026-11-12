"""Runbook YAML schema (Pydantic) — extract-only after API removal.

A runbook declares which extractors to run per company and where to
write the resulting knowledge blob. Optional fields use empty defaults
(`""`, `[]`, sentinel empty model) instead of `Optional[X] = None`.

`_validate_extractor_name` checks against the runtime registry rather
than a hardcoded `Literal[...]`, so adding a new extractor doesn't
require a schema edit."""

from __future__ import annotations

import re
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from briar.auth.config import AuthBlock


class _Strict(BaseModel):
    """All schema models forbid unknown keys — typos surface at load
    time with a locator-aware Pydantic error, never silently."""

    model_config = ConfigDict(extra="forbid")


class ExtractEntry(_Strict):
    """Per-company extractor selection with its kind-specific args."""

    name: str
    args: Dict[str, Any] = Field(default_factory=dict)

    # Args that left for the company `auth:` block (spec 005, I4).
    MOVED_ARGS: ClassVar[Tuple[str, ...]] = ("aws_extract_profile", "cloud_profile")

    @field_validator("args")
    @classmethod
    def _reject_moved_args(cls, args: Dict[str, Any]) -> Dict[str, Any]:
        moved = [key for key in cls.MOVED_ARGS if key in args]
        if moved:
            raise PydanticCustomError(
                "moved_arg", "`{keys}` moved to the company `auth:` block: `aws.profile`, `gcp.project` or `azure.subscription`", {"keys": ", ".join(moved)}
            )
        return args

    @field_validator("name")
    @classmethod
    def _validate_extractor_name(cls, value: str) -> str:
        """Check against the live `EXTRACTORS` registry — single source
        of truth. Lazy import to avoid a cycle (extract/__init__.py
        imports the extractors, which transitively touch the runbook
        models on test discovery)."""
        from briar.extract import EXTRACTORS

        if value not in EXTRACTORS:
            known = ", ".join(sorted(EXTRACTORS.keys()))
            raise ValueError(f"unknown extractor {value!r}; known: {known}")
        return value


class ScheduleEntry(_Strict):
    """One scheduled task — a named group of extractors fired by the
    in-process `schedule` library. See `EveryParser` for the DSL."""

    task: str
    every: str
    extract: List[ExtractEntry] = Field(default_factory=list)


class KnowledgeBinding(BaseModel):
    """Where this company's knowledge blob lands on disk. An empty
    `name` means "not configured" — callers fall back to a default."""

    model_config = ConfigDict(extra="ignore")

    store: Literal["file", "postgres"] = "file"
    name: str = ""
    root: str = ""
    # Backend-specific config. Today the only key is `inventory` (also write
    # the JSON inventory blob).
    config: Dict[str, str] = Field(default_factory=dict)

    # C11: `postgres` stays in the Literal only so a 1.x runbook gets this
    # message instead of a bare "input should be 'file'".
    REMOVED_STORES: ClassVar[Tuple[str, ...]] = ("postgres",)

    @field_validator("store")
    @classmethod
    def _reject_removed_store(cls, value: str) -> str:
        if value in cls.REMOVED_STORES:
            raise PydanticCustomError("removed_store", "`store: {store}` was removed in briar 2.0; use `store: file` or delete the line", {"store": value})
        return value


class StatusNames(_Strict):
    """The ticket status names `briar serve` moves a ticket to (S26); empty: no move."""

    started: str = ""
    review: str = ""


class ServeSettings(_Strict):
    """`companies.<company>.serve` of a runbook: what `briar serve` watches and the limits
    of one task (decisions S5, S11 to S16, S24 to S27 in
    `docs/decisions/2026-11-11-briar-2-serve-pipeline.md`, V1, V11, V13 in
    `docs/decisions/2026-11-11-briar-2-review-routine.md`)."""

    # Required (S5, S24): nobody spends on a model or a limit they did not choose.
    model: str = Field(min_length=1)
    max_usd: float = Field(gt=0)
    max_loops: int = Field(ge=1)
    ci_wait_minutes: int = Field(ge=1)
    max_review_rounds: int = Field(ge=1)
    max_parallel: int = Field(default=3, ge=1)
    poll_minutes: int = Field(default=5, ge=1)
    # GitHub repos to work in: an open issue there assigned to the token owner is a ticket.
    repos: List[str] = Field(default_factory=list)
    # Jira project key -> the repo its tickets are built in (S13).
    jira: Dict[str, str] = Field(default_factory=dict)
    verify: List[str] = Field(default_factory=list)
    bash: List[str] = Field(default_factory=list)
    priority_labels: List[str] = Field(default_factory=list)
    # Bot logins whose PR comments briar answers (V1); people need write access.
    review_bots: List[str] = Field(default_factory=list)
    # The domains the sandboxed bash of the agent may reach (V13); none by default.
    allowed_domains: List[str] = Field(default_factory=list)
    status: StatusNames = Field(default_factory=StatusNames)
    branch: str = "briar/{key}"
    title: str = "{key}: {title}"

    REPO_NAME: ClassVar["re.Pattern[str]"] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
    PROJECT_KEY: ClassVar["re.Pattern[str]"] = re.compile(r"^[A-Z][A-Z0-9_]*$")
    DOMAIN: ClassVar["re.Pattern[str]"] = re.compile(r"^(\*\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
    BOT_LOGIN: ClassVar["re.Pattern[str]"] = re.compile(r"^[A-Za-z0-9-]+\[bot\]$")

    @model_validator(mode="after")
    def _check_targets(self) -> "ServeSettings":
        if not self.repos and not self.jira:
            raise PydanticCustomError("nothing_to_watch", "set `repos` or `jira` under `serve`: there is nothing to watch", {})
        bad = [name for name in self.all_repos() if not self.REPO_NAME.match(name)] + [key for key in self.jira if not self.PROJECT_KEY.match(key)]
        if bad:
            raise PydanticCustomError("bad_target", "not an owner/repo name or a Jira project key: {names}", {"names": ", ".join(bad)})
        return self

    @model_validator(mode="after")
    def _check_names(self) -> "ServeSettings":
        bad = [name for name in self.allowed_domains if not self.DOMAIN.match(name)] + [name for name in self.review_bots if not self.BOT_LOGIN.match(name)]
        if bad:
            raise PydanticCustomError("bad_name", "not a domain (`pypi.org`) or a bot login (`renovate[bot]`): {names}", {"names": ", ".join(bad)})
        return self

    def all_repos(self) -> List[str]:
        """Every repo briar works in: `repos`, then the repos of `jira`, each once."""
        return list(dict.fromkeys([*self.repos, *self.jira.values()]))


class CompanyEntry(BaseModel):
    """Company-level extraction config. Ignores unknown keys so legacy
    YAMLs that still carry `runbooks:`, `defaults:`, etc. still load."""

    model_config = ConfigDict(extra="ignore")

    profile: str = ""
    workspace_id: str = ""
    knowledge_file: str = ""
    knowledge: KnowledgeBinding = Field(default_factory=KnowledgeBinding)
    extract: List[ExtractEntry] = Field(default_factory=list)
    schedules: List[ScheduleEntry] = Field(default_factory=list)
    # The login choices of this company (spec 005, G2, G3): same keys as `[auth]` of
    # `.briar.toml`, and the only ones `briar serve` reads for a runbook.
    auth: AuthBlock = Field(default_factory=AuthBlock)
    # What `briar serve` watches for this company (spec 003); None: serve leaves it alone.
    serve: Optional[ServeSettings] = None

    # Blocks only the 1.x agent read (spec 006, C5, C20): a runbook that still
    # has one stops the load instead of being silently ignored (FR-006a).
    REMOVED_KEYS: ClassVar[Tuple[str, ...]] = ("mcp", "git_identity", "messages")

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_keys(cls, company_yaml: Any) -> Any:
        removed = [key for key in cls.REMOVED_KEYS if isinstance(company_yaml, dict) and key in company_yaml]
        if removed:
            raise PydanticCustomError("removed_key", "`{keys}` was removed in briar 2.0; delete it from the runbook", {"keys": ", ".join(removed)})
        return company_yaml


class RunbookFile(_Strict):
    version: int = 1
    companies: Dict[str, CompanyEntry] = Field(min_length=1)
