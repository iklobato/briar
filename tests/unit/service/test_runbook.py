"""Runbook service — read, validate, and gated config writes."""

from __future__ import annotations

import pytest

from briar.errors import ConfigError
from briar.iac.runbook import RunbookFile, load_runbook_file, save_runbook_file
from briar.service import GateMode
from briar.service import runbook as rs

_RB = {
    "companies": {
        "acme": {
            "mcp": {
                "github": {"transport": "stdio", "command": "docker", "env": {"GITHUB_TOKEN": "GITHUB_TOKEN"}, "enabled": True},
            }
        }
    }
}


def _write(tmp_path):
    path = tmp_path / "rb.yaml"
    save_runbook_file(path, RunbookFile.model_validate(_RB))
    return path


def test_to_dict_roundtrips_through_schema(tmp_path) -> None:
    rb = load_runbook_file(_write(tmp_path))
    d = rs.to_dict(rb)
    assert d["companies"]["acme"]["mcp"]["github"]["command"] == "docker"


def test_validate_ok(tmp_path) -> None:
    verdict = rs.validate(_write(tmp_path))
    assert verdict == {"valid": True, "error": "", "companies": ["acme"]}


def test_validate_reports_bad_file(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("companies: {}\n")  # min_length=1 violation
    verdict = rs.validate(bad)
    assert verdict["valid"] is False
    assert verdict["error"]


def test_set_mcp_enabled_dry_run_does_not_write(tmp_path) -> None:
    path = _write(tmp_path)
    out = rs.set_mcp_enabled(path, company="acme", handle="github", enabled=False, gate=GateMode.DRY_RUN)
    assert out.executed is False
    assert "would disable" in out.summary
    # Unchanged on disk.
    assert load_runbook_file(path).companies["acme"].mcp["github"].enabled is True


def test_set_mcp_enabled_execute_persists(tmp_path) -> None:
    path = _write(tmp_path)
    out = rs.set_mcp_enabled(path, company="acme", handle="github", enabled=False)
    assert out.executed is True
    assert load_runbook_file(path).companies["acme"].mcp["github"].enabled is False


def test_set_mcp_enabled_unknown_company(tmp_path) -> None:
    with pytest.raises(ConfigError, match="unknown company"):
        rs.set_mcp_enabled(_write(tmp_path), company="ghost", handle="github", enabled=False)


def test_set_mcp_enabled_unknown_handle(tmp_path) -> None:
    with pytest.raises(ConfigError, match="unknown mcp server"):
        rs.set_mcp_enabled(_write(tmp_path), company="acme", handle="ghost", enabled=False)
