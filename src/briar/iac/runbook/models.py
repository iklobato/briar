"""Runbook YAML schema (Pydantic) — extract-only after API removal.

A runbook declares which extractors to run per company and where to
write the resulting knowledge blob. Optional fields use empty defaults
(`""`, `[]`, sentinel empty model) instead of `Optional[X] = None`.

The two name-validation field validators (`_validate_extractor_name`,
`_validate_store_name`) check against the runtime registries rather
than a hardcoded `Literal[...]` — adding a new extractor / store
backend doesn't require a schema edit."""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Literal, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


class _Strict(BaseModel):
    """All schema models forbid unknown keys — typos surface at load
    time with a locator-aware Pydantic error, never silently."""

    model_config = ConfigDict(extra="forbid")


class ExtractEntry(_Strict):
    """Per-company extractor selection with its kind-specific args."""

    name: str
    args: Dict[str, Any] = Field(default_factory=dict)

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
    # Backend-specific config (mirrors MessageBinding.config). Each store's
    # `from_binding` decides which keys it understands. Today: `postgres`
    # reads `dsn_env` (an env-var NAME holding the DSN — never the DSN
    # itself, which stays out of version control).
    config: Dict[str, str] = Field(default_factory=dict)

    @field_validator("store")
    @classmethod
    def _validate_store_name(cls, value: str) -> str:
        """Belt-and-suspenders sanity check against the live
        `KnowledgeStoreRegistry`. The Literal annotation catches typos
        at parse time; this validator catches drift if a backend is
        ever removed from the registry while still allowed by Literal."""
        from briar.storage import KnowledgeStoreRegistry

        kinds = KnowledgeStoreRegistry.names()
        if value not in kinds:
            raise ValueError(f"unknown knowledge store {value!r}; known: {', '.join(sorted(kinds))}")
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
