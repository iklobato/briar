"""briar's own MCP **server** — the inverse of `briar.mcp` (the client).

`briar.mcp` connects *out* to external MCP servers for the agent loop. This
package goes the other way: it exposes briar's features and configuration *as*
an MCP server, so any MCP host (Claude Desktop, Cursor, `briar chat`, a remote
client) can drive briar through tools and resources.

Every operation routes through `briar.service`, so the server, the CLI, and
the dashboard share one code path. Mutating/expensive tools are gated: they
default to a dry-run preview and only act when called with ``confirm=true``.
"""

from __future__ import annotations

from briar.mcpserver.server import ServerContext, build_server

__all__ = ["ServerContext", "build_server"]
