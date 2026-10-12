"""MCP error type — its own leaf module so `_manager` and `_tool` can
both import it without a cycle."""

from __future__ import annotations

from briar.errors import CliError


class McpError(CliError):
    """Raised for MCP setup/connection failures the operator must see — a
    missing SDK, a supervisor that never came up, a call to a server that
    failed to connect or timed out."""
