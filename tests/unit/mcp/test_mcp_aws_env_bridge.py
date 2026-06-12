"""The credential bridge that lets the agent reach AWS via the AWS MCP
server (config-only integration).

briar stores AWS creds per-company as ``AWS_<COMPANY>_*``; the AWS Labs
API MCP server reads the STANDARD ``AWS_*`` names. The runbook `mcp:`
block's ``env:`` map closes that gap — value = the briar env-var NAME,
resolved to its value at run time. These tests pin that resolution
(McpClientManager._resolve_env) so the example config in
``examples/all_features.yaml`` provably wires real creds through.
"""

from __future__ import annotations

from pathlib import Path

from briar.iac.runbook import load_runbook_file
from briar.mcp import McpClientManager


def test_resolve_env_bridges_per_company_aws_creds(monkeypatch) -> None:
    monkeypatch.setenv("AWS_ACME_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_ACME_SECRET_ACCESS_KEY", "shhh")
    monkeypatch.setenv("AWS_ACME_REGION", "us-east-1")
    monkeypatch.delenv("AWS_ACME_SESSION_TOKEN", raising=False)  # static keys → no session token

    mapping = {
        "AWS_ACCESS_KEY_ID": "AWS_ACME_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY": "AWS_ACME_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN": "AWS_ACME_SESSION_TOKEN",
        "AWS_REGION": "AWS_ACME_REGION",
    }

    resolved = McpClientManager._resolve_env(mapping)

    # Per-company values land under the STANDARD AWS names the server reads.
    assert resolved == {
        "AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "shhh",
        "AWS_REGION": "us-east-1",
    }
    # An unset var (session token, for static keys) is DROPPED, never passed
    # as an empty string — a half-credential must not silently reach AWS.
    assert "AWS_SESSION_TOKEN" not in resolved


def test_example_runbook_aws_mcp_block_is_valid() -> None:
    rb = load_runbook_file(Path("examples/all_features.yaml"))
    aws = rb.companies["acme"].mcp["aws"]

    assert aws.transport == "stdio"
    assert aws.command == "uvx"
    assert "awslabs.aws-api-mcp-server@latest" in aws.args
    assert aws.tools == ["call_aws", "suggest_aws_commands"]
    # The env map references briar's per-company cred names (not literals).
    assert aws.env["AWS_ACCESS_KEY_ID"] == "AWS_ACME_ACCESS_KEY_ID"
    assert aws.purpose  # non-empty → the router can reason about it
