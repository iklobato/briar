"""MCP (Model Context Protocol) client support for the agent runtime.

Connects to MCP servers declared in a company's runbook ``mcp:`` block,
lists their tools, and exposes each as an agent tool (`mcp__<server>__<tool>`).
See `briar.iac.runbook.models.McpServerBinding` for the config schema.
"""

from __future__ import annotations

from briar.mcp._errors import McpError
from briar.mcp._manager import McpClientManager
from briar.mcp._tool import McpTool

__all__ = ["McpClientManager", "McpError", "McpTool"]
