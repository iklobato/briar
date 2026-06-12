"""Schema tests for the runbook ``mcp:`` block (`McpServerBinding`).

Assert the validator's observable decisions: transport-appropriate
required fields, unknown-key rejection (strict model), the ``enabled``
default, and that the block round-trips through a full ``RunbookFile``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from briar.iac.runbook.models import McpServerBinding, RunbookFile


def test_stdio_requires_command() -> None:
    with pytest.raises(ValidationError, match="stdio server requires a non-empty `command`"):
        McpServerBinding(transport="stdio")


def test_http_requires_url() -> None:
    with pytest.raises(ValidationError, match="http server requires a non-empty `url`"):
        McpServerBinding(transport="http")


def test_stdio_binding_defaults() -> None:
    b = McpServerBinding(transport="stdio", command="docker", args=["run", "-i"])
    assert b.command == "docker"
    assert b.args == ["run", "-i"]
    assert b.enabled is True
    assert b.env == {}
    assert b.tools == []


def test_http_binding_with_headers() -> None:
    b = McpServerBinding(transport="http", url="https://mcp.example/mcp", headers={"Authorization": "TOK_ENV"})
    assert b.url == "https://mcp.example/mcp"
    # Header value is an env-var NAME, not a literal secret.
    assert b.headers == {"Authorization": "TOK_ENV"}


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="bogus"):
        McpServerBinding(transport="stdio", command="x", bogus=1)


def test_default_transport_is_stdio() -> None:
    b = McpServerBinding(command="npx")
    assert b.transport == "stdio"


def test_purpose_defaults_empty_and_accepts_text() -> None:
    assert McpServerBinding(command="npx").purpose == ""
    b = McpServerBinding(command="npx", purpose="GitHub issues and PRs")
    assert b.purpose == "GitHub issues and PRs"


def test_archetypes_defaults_empty_and_accepts_list() -> None:
    assert McpServerBinding(command="npx").archetypes == []
    b = McpServerBinding(command="npx", archetypes=["engineer", "pr-fixer"])
    assert b.archetypes == ["engineer", "pr-fixer"]


def test_roundtrips_through_runbook_file() -> None:
    rb = RunbookFile.model_validate(
        {
            "companies": {
                "acme": {
                    "mcp": {
                        "github": {
                            "transport": "stdio",
                            "command": "docker",
                            "args": ["run", "-i", "--rm", "ghcr.io/github/github-mcp-server"],
                            "env": {"GITHUB_TOKEN": "GITHUB_TOKEN"},
                            "tools": ["search_issues"],
                        },
                        "off": {"transport": "stdio", "command": "x", "enabled": False},
                    }
                }
            }
        }
    )
    mcp = rb.companies["acme"].mcp
    assert set(mcp) == {"github", "off"}
    assert mcp["github"].env == {"GITHUB_TOKEN": "GITHUB_TOKEN"}
    assert mcp["github"].tools == ["search_issues"]
    assert mcp["off"].enabled is False


def test_company_without_mcp_block_defaults_empty() -> None:
    rb = RunbookFile.model_validate({"companies": {"acme": {}}})
    assert rb.companies["acme"].mcp == {}
