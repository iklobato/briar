"""End-to-end: the REAL `McpClientManager` against a REAL stdio MCP server.

Spawns ``mcp_echo_server.py`` as a subprocess via the stdio transport and
drives the genuine protocol handshake (`initialize` → `tools/list` →
`tools/call`) through the sync bridge. Everything runs for real:

  * the background event-loop thread + supervisor coroutine open the
    session and list tools;
  * `McpTool.run` blocks the calling (test) thread, round-trips JSON-RPC
    to the subprocess, and flattens the content blocks to a string;
  * the server's failing tool surfaces as a `ToolError`;
  * the optional `tools:` allowlist narrows what gets bound;
  * `close()` tears the subprocess + loop thread down.

Skips cleanly when the `mcp` extra isn't installed, so the base suite
stays green. MCP spec: https://modelcontextprotocol.io/specification
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="requires the `mcp` extra: pip install 'briar-cli[mcp]'")

from briar.agent.tools import ToolError  # noqa: E402
from briar.iac.runbook.models import McpServerBinding  # noqa: E402
from briar.mcp import McpClientManager  # noqa: E402

pytestmark = pytest.mark.integration

_SERVER = str(Path(__file__).parent / "mcp_echo_server.py")


def _stdio_binding(*, tools: list[str] | None = None) -> McpServerBinding:
    # Launch the server with THIS interpreter (the one that has `mcp`),
    # so the subprocess can import the SDK from the same site-packages.
    return McpServerBinding(transport="stdio", command=sys.executable, args=[_SERVER], tools=tools or [])


@pytest.fixture
def manager():
    mgr = McpClientManager({"echo": _stdio_binding()})
    try:
        yield mgr
    finally:
        mgr.close()


def test_lists_real_server_tools(manager) -> None:
    tools = manager.start()
    names = {t.name for t in tools}
    assert names == {"mcp__echo__echo", "mcp__echo__add", "mcp__echo__boom"}


def test_echo_round_trip(manager) -> None:
    tools = {t.name: t for t in manager.start()}
    assert tools["mcp__echo__echo"].run(text="hello world") == "hello world"


def test_typed_args_and_schema(manager) -> None:
    tools = {t.name: t for t in manager.start()}
    add = tools["mcp__echo__add"]
    # Real input schema came over the wire from the server.
    assert "a" in add.INPUT_SCHEMA.get("properties", {})
    assert add.run(a=3, b=4).strip() == "7"


def test_failing_tool_becomes_tool_error(manager) -> None:
    tools = {t.name: t for t in manager.start()}
    with pytest.raises(ToolError, match="mcp__echo__boom"):
        tools["mcp__echo__boom"].run()


def test_tools_allowlist_narrows_binding() -> None:
    mgr = McpClientManager({"echo": _stdio_binding(tools=["echo"])})
    try:
        names = {t.name for t in mgr.start()}
        assert names == {"mcp__echo__echo"}
    finally:
        mgr.close()


def test_close_is_idempotent(manager) -> None:
    manager.start()
    manager.close()
    manager.close()  # second close must not raise
