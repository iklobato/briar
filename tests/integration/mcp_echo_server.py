"""A minimal REAL MCP server over stdio, for integration tests.

Built with the same `mcp` SDK the client bridge talks to, so the test
exercises the genuine JSON-RPC handshake (`initialize` → `tools/list` →
`tools/call`) across a real subprocess — not a mock. Run as a script;
the `McpClientManager` spawns it via the stdio transport.

Tools:
  * ``echo(text)``   — returns the text back (text content block).
  * ``add(a, b)``    — returns the sum (exercises typed args + schema).
  * ``boom()``       — raises, so the client sees an MCP error result
                       (which `McpTool.run` must surface as a ToolError).
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("briar-echo")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the given text straight back."""
    return text


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers and return the sum."""
    return a + b


@mcp.tool()
def boom() -> str:
    """Always fail — used to test the error path."""
    raise RuntimeError("intentional failure")


if __name__ == "__main__":
    mcp.run()
