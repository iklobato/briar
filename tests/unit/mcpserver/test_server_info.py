"""The `serverInfo` an MCP client sees on `initialize` names briar, not the `mcp` library."""

from __future__ import annotations

import pytest

pytest.importorskip("mcp", reason="requires the `mcp` extra: pip install 'briar-cli[mcp]'")

from briar import __version__  # noqa: E402
from briar.mcpserver import ServerContext, build_server  # noqa: E402


def test_initialize_reports_briar_version() -> None:
    server = build_server(ServerContext(root="/tmp/briar-test"))
    options = server._mcp_server.create_initialization_options()
    assert options.server_name == "briar"
    assert options.server_version == __version__
