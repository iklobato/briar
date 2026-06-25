"""Runbook config operations — read, validate, and gated writes.

Wraps `load_runbook_file` / `save_runbook_file` so the MCP server and
dashboard can inspect and edit runbook YAML through one code path. Reads
are safe to expose: the schema only ever stores env-var *names*, never
secret values (see `iac/runbook/models.py`), so `to_dict` returns the
model verbatim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from briar.errors import ConfigError
from briar.iac.runbook import RunbookFile, load_runbook_file, save_runbook_file
from briar.service._gating import GateMode, GateResult


def load(path: str | Path) -> RunbookFile:
    return load_runbook_file(Path(path))


def to_dict(rb: RunbookFile) -> Dict[str, Any]:
    """Schema view as JSON. Safe to return over the wire — env-var-name
    indirection means no secret literals live in the model."""
    return rb.model_dump(mode="json", exclude_defaults=True)


def validate(path: str | Path) -> Dict[str, Any]:
    """Parse + schema-check a runbook without raising. Returns a verdict
    dict the way a linter would, so a UI can show the error inline."""
    try:
        rb = load_runbook_file(Path(path))
    except ConfigError as exc:
        return {"valid": False, "error": str(exc), "companies": []}
    return {"valid": True, "error": "", "companies": sorted(rb.companies)}


def save(path: str | Path, rb: RunbookFile, *, gate: GateMode = GateMode.EXECUTE) -> GateResult:
    if gate is GateMode.DRY_RUN:
        return GateResult.previewed(f"would write runbook to {path} ({len(rb.companies)} company/companies)")
    save_runbook_file(Path(path), rb)
    return GateResult.performed(f"wrote runbook to {path}", {"companies": sorted(rb.companies)})


def set_mcp_enabled(
    path: str | Path,
    *,
    company: str,
    handle: str,
    enabled: bool,
    gate: GateMode = GateMode.EXECUTE,
) -> GateResult:
    """Flip an MCP server's `enabled` flag in a company's `mcp:` block."""
    rb = load_runbook_file(Path(path))
    entry = rb.companies.get(company)
    if entry is None:
        raise ConfigError(f"unknown company {company!r}; known: {', '.join(sorted(rb.companies))}")
    server = entry.mcp.get(handle)
    if server is None:
        raise ConfigError(f"unknown mcp server {handle!r} for company {company!r}; known: {', '.join(sorted(entry.mcp))}")

    verb = "enable" if enabled else "disable"
    if gate is GateMode.DRY_RUN:
        return GateResult.previewed(f"would {verb} mcp server {handle!r} for company {company!r} (currently {'on' if server.enabled else 'off'})")
    server.enabled = enabled
    save_runbook_file(Path(path), rb)
    return GateResult.performed(f"{verb}d mcp server {handle!r} for company {company!r}", {"company": company, "handle": handle, "enabled": enabled})
