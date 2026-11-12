"""Runbook YAML schema (Pydantic) — extract-only after API removal.

A runbook declares which extractors to run per company and where to
write the resulting knowledge blob. Optional fields use empty defaults
(`""`, `[]`, sentinel empty model) instead of `Optional[X] = None`.

`_validate_extractor_name` checks against the runtime registry rather
than a hardcoded `Literal[...]`, so adding a new extractor doesn't
require a schema edit."""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Literal, Tuple

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
    # `.briar.toml`, and the only ones `briar runbook` reads.
    auth: AuthBlock = Field(default_factory=AuthBlock)

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
