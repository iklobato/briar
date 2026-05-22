"""Runbook YAML schema (Pydantic) — extract-only after API removal.

A runbook declares which extractors to run per company and where to
write the resulting knowledge blob. Optional fields use empty defaults
(`""`, `[]`, sentinel empty model) instead of `Optional[X] = None`.

The two name-validation field validators (`_validate_extractor_name`,
`_validate_store_name`) check against the runtime registries rather
than a hardcoded `Literal[...]` — adding a new extractor / store
backend doesn't require a schema edit. See ARCHITECTURE.md finding
#5 + #6."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    store: str = "file"
    name: str = ""
    root: str = ""

    @field_validator("store")
    @classmethod
    def _validate_store_name(cls, value: str) -> str:
        """Check against the live `KnowledgeStoreRegistry.STORES`
        registry. Adding a new backend (S3, etc.) requires zero edits
        to this schema."""
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


class RunbookFile(_Strict):
    version: int = 1
    companies: Dict[str, CompanyEntry] = Field(min_length=1)
