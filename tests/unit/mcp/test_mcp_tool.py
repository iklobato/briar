"""Behavioural tests for `McpTool` — the agent-shaped wrapper over one
MCP server tool.

These assert what the tool *produces* given a manager that returns a
given `CallToolResult`-shaped object: the namespaced name, the
text-flattening of content blocks, the error→`ToolError` mapping, and
that `run(**kwargs)` forwards kwargs to `manager.call`. The manager is a
hand-built fake (no `mcp` SDK, no event loop) so the test is about the
wrapper's contract, not the bridge.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from briar.agent.tools import ToolError
from briar.mcp._errors import McpError
from briar.mcp._tool import McpTool


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _result(content: List[Any], *, is_error: bool = False) -> SimpleNamespace:
    return SimpleNamespace(content=content, isError=is_error)


class FakeManager:
    """Records the last call and replays a queued result (or raises)."""

    def __init__(self, result: Any = None, *, raises: Exception | None = None) -> None:
        self._result = result
        self._raises = raises
        self.calls: List[Dict[str, Any]] = []

    def call(self, server: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        self.calls.append({"server": server, "tool": tool_name, "args": arguments})
        if self._raises is not None:
            raise self._raises
        return self._result


def _tool(manager: FakeManager, *, schema: Dict[str, Any] | None = None) -> McpTool:
    return McpTool(manager, "github", "search_issues", "Search issues.", schema or {"type": "object"})


def test_name_is_namespaced() -> None:
    tool = _tool(FakeManager(_result([_text("x")])))
    assert tool.name == "mcp__github__search_issues"
    assert tool.description == "Search issues."
    assert tool.INPUT_SCHEMA == {"type": "object"}


def test_run_forwards_kwargs_and_returns_text() -> None:
    mgr = FakeManager(_result([_text("issue #1"), _text("issue #2")]))
    tool = _tool(mgr)

    out = tool.run(query="bug", limit=5)

    assert out == "issue #1\nissue #2"
    assert mgr.calls == [{"server": "github", "tool": "search_issues", "args": {"query": "bug", "limit": 5}}]


def test_error_result_becomes_tool_error() -> None:
    tool = _tool(FakeManager(_result([_text("rate limited")], is_error=True)))
    with pytest.raises(ToolError, match="returned an error: rate limited"):
        tool.run()


def test_empty_content_returns_placeholder() -> None:
    tool = _tool(FakeManager(_result([])))
    assert tool.run() == "(empty result)"


def test_non_text_block_is_noted_not_dropped() -> None:
    tool = _tool(FakeManager(_result([SimpleNamespace(type="image", mimeType="image/png")])))
    assert tool.run() == "[image content omitted]"


def test_resource_block_uses_embedded_text() -> None:
    resource = SimpleNamespace(type="resource", resource=SimpleNamespace(text="file body", uri="file://x"))
    tool = _tool(FakeManager(_result([resource])))
    assert tool.run() == "file body"


def test_manager_error_becomes_tool_error() -> None:
    tool = _tool(FakeManager(raises=McpError("server not connected")))
    with pytest.raises(ToolError, match="mcp__github__search_issues: server not connected"):
        tool.run()


def test_empty_description_gets_fallback() -> None:
    tool = McpTool(FakeManager(_result([_text("ok")])), "sentry", "list_errors", "", {})
    assert "list_errors" in tool.description
    assert tool.INPUT_SCHEMA == {"type": "object", "properties": {}}


def test_purpose_is_folded_into_description() -> None:
    tool = McpTool(
        FakeManager(_result([_text("ok")])),
        "sentry",
        "list_errors",
        "List recent errors.",
        {},
        purpose="Production error telemetry",
    )
    assert "List recent errors." in tool.description
    assert "When to use: Production error telemetry" in tool.description
    assert tool.server == "sentry"
    assert tool.purpose == "Production error telemetry"


def test_no_purpose_leaves_description_plain() -> None:
    tool = McpTool(FakeManager(_result([_text("ok")])), "sentry", "list_errors", "List recent errors.", {})
    assert tool.description == "List recent errors."
