"""McpTool — a remote MCP server tool wrapped in the agent's tool shape.

The agent's tools are duck-typed: `name`, `description`, `INPUT_SCHEMA`
(JSON Schema) and a synchronous `run(**kwargs) -> str` that raises
`ToolError` on failure. `McpTool` makes one tool advertised by an MCP
server look exactly like that, delegating execution to the
`McpClientManager` (which bridges to the async session).

Naming: `mcp__<server>__<tool>` mirrors Claude Code's own MCP tool
namespacing and guarantees no collision with the built-in `bash` /
`read_file` / `edit_file` / `send_message` tools.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from briar.agent.tools import ToolError
from briar.mcp._errors import McpError

log = logging.getLogger(__name__)


def _stringify(result: Any) -> str:
    """Flatten an MCP `CallToolResult.content` list into one string.

    The agent's tool contract returns a single string that becomes a
    `tool_result` block. MCP returns a list of typed content blocks; we
    join text, and note non-text blocks rather than dropping them
    silently."""
    parts = []
    for block in getattr(result, "content", None) or []:
        btype = getattr(block, "type", "")
        if btype == "text":
            parts.append(getattr(block, "text", ""))
        elif btype == "resource":
            resource = getattr(block, "resource", None)
            text = getattr(resource, "text", None)
            parts.append(text if text is not None else f"[resource {getattr(resource, 'uri', '')}]")
        else:
            parts.append(f"[{btype or 'unknown'} content omitted]")
    return "\n".join(parts).strip()


class McpTool:
    def __init__(
        self,
        manager: Any,  # McpClientManager — typed as Any to keep this module a leaf (no _manager import cycle)
        server: str,
        tool_name: str,
        description: str,
        input_schema: Dict[str, Any],
        purpose: str = "",
    ) -> None:
        self._manager = manager
        self.server = server
        self.purpose = purpose
        self._tool_name = tool_name
        self.name = f"mcp__{server}__{tool_name}"
        base = description or f"MCP tool {tool_name!r} from server {server!r}."
        # Fold the server's `purpose` into the advertised description so the
        # model's tool-selection judgment sees *when to reach for this
        # source*, not just what it does (Lever 1 of MCP routing).
        self.description = f"{base}\n\nWhen to use: {purpose}" if purpose else base
        self.INPUT_SCHEMA = input_schema or {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:
        try:
            result = self._manager.call(self.server, self._tool_name, kwargs)
        except McpError as exc:
            raise ToolError(f"{self.name}: {exc}") from exc

        if getattr(result, "isError", False):
            raise ToolError(f"{self.name} returned an error: {_stringify(result) or '(no detail)'}")
        return _stringify(result) or "(empty result)"
