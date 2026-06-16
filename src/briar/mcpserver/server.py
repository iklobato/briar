"""Build briar's MCP server (a `FastMCP` instance) from a `ServerContext`.

The context pins *which* knowledge store and runbook the tools operate on —
resolved once from `briar mcp serve` flags, then closed over by every tool so
the LLM never has to (and never can) pass a store backend or file root itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from briar.mcpserver._tools import register_tools


@dataclass(frozen=True)
class ServerContext:
    """What the server's tools act on. Set once from CLI flags."""

    store: str = "file"
    root: str = "./knowledge"
    runbook_path: Optional[str] = None


def build_server(ctx: ServerContext):
    """Construct and return the configured `FastMCP` server.

    Imported lazily so importing this module (e.g. for the command registry)
    does not hard-require the optional `mcp` extra."""
    from mcp.server.fastmcp import FastMCP

    server: Any = FastMCP("briar")
    register_tools(server, ctx)
    return server
