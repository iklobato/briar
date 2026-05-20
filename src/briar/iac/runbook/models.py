"""Runbook YAML schema (Pydantic) — extract-only after API removal.

A runbook now declares which extractors to run per company and where
to write the resulting knowledge blob. The old apply-side declarations
(runbooks, sources, triggers) were removed when the CLI dropped its
remote-call surface."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """All schema models forbid unknown keys — typos surface at load
    time with a locator-aware Pydantic error, never silently."""
    model_config = ConfigDict(extra="forbid")


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


class KnowledgeBinding(BaseModel):
    """Where this company's knowledge blob lands on disk.

    Only the local file store is supported now; `mode` and the legacy
    `briar-api` backend were removed but are tolerated in YAML for
    backwards compatibility."""

    model_config = ConfigDict(extra="ignore")

    store: Literal["file"] = "file"
    name: str
    root: Optional[str] = None


class CompanyEntry(BaseModel):
    """Company-level extraction config.

    Unlike the strict siblings, this model **ignores** unknown keys so
    legacy YAMLs that still carry `runbooks:`, `defaults:`, `api_base:`
    etc. continue to load without manual cleanup. Those fields just no
    longer drive anything on the CLI side."""

    model_config = ConfigDict(extra="ignore")

    profile: Optional[str] = None
    workspace_id: Optional[str] = None

    knowledge_file: Optional[str] = None
    knowledge: Optional[KnowledgeBinding] = None

    extract: List[ExtractEntry] = Field(default_factory=list)


class RunbookFile(_Strict):
    version: int = 1
    companies: Dict[str, CompanyEntry] = Field(min_length=1)
