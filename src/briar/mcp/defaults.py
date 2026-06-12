"""Built-in MCP servers enabled by default for every agent run.

These are credential-free, no-network reasoning/utility servers that help
any task, so they're on unless the operator opts out (``--no-default-mcp``
or ``BRIAR_NO_DEFAULT_MCP``). A runbook overrides one by declaring the same
handle in its ``mcp:`` block.

They are ALWAYS-ON: connected unconditionally and excluded from the Lever-4
router — routing between two trivial local tools would just waste an LLM
call, so the router only ever prunes the heavier, runbook-declared servers.

Dependency note: ``think`` needs ``npx`` (Node), ``time`` needs ``uvx``
(uv). On a host without those the server simply fails to connect (logged,
the run continues); deployments that lack them should opt out to avoid the
per-run warning.
"""

from __future__ import annotations

from typing import Dict

from briar.iac.runbook.models import McpServerBinding

DEFAULT_MCP_SERVERS: Dict[str, McpServerBinding] = {
    # "Think step by step" — structured reasoning aid. No creds, no network.
    "think": McpServerBinding(
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sequential-thinking"],
        purpose="Structured step-by-step reasoning for breaking down complex, multi-step tasks",
    ),
    # Current date/time + timezone math. Tiny, no creds, no network.
    "time": McpServerBinding(
        transport="stdio",
        command="uvx",
        args=["mcp-server-time"],
        purpose="Current date/time and timezone conversion",
    ),
}
