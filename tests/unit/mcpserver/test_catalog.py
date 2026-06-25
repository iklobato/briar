"""The MCP server's tool catalog and its gating shape.

Builds the FastMCP server in-process (no subprocess) and asserts that the
expected tools exist, that mutating tools take a `confirm` parameter, and
that read tools do not — the contract the dry-run gate depends on.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("mcp", reason="requires the `mcp` extra: pip install 'briar-cli[mcp]'")

from briar.mcpserver import ServerContext, build_server  # noqa: E402

_READ_TOOLS = {"version", "knowledge_list", "knowledge_get", "knowledge_categories", "runbook_get", "runbook_validate"}
_GATED_TOOLS = {"knowledge_put", "knowledge_delete", "mcp_server_set_enabled", "extract_run"}


@pytest.fixture
def tools():
    server = build_server(ServerContext(root="/tmp/briar-test"))
    listed = asyncio.run(server.list_tools())
    return {t.name: t for t in listed}


def test_catalog_covers_reads_and_gated(tools) -> None:
    assert _READ_TOOLS <= set(tools)
    assert _GATED_TOOLS <= set(tools)


def test_gated_tools_take_confirm(tools) -> None:
    for name in _GATED_TOOLS:
        props = tools[name].inputSchema.get("properties", {})
        assert "confirm" in props, f"{name} must expose a confirm gate"


def test_read_tools_have_no_confirm(tools) -> None:
    for name in _READ_TOOLS:
        props = tools[name].inputSchema.get("properties", {})
        assert "confirm" not in props, f"read tool {name} must not be gated"
