"""End-to-end: briar's OWN MCP server, driven by the real `McpClientManager`.

Spawns ``briar mcp serve --transport stdio`` as a subprocess and round-trips
the genuine protocol (`initialize` → `tools/list` → `tools/call`) through the
sync client bridge — the same path `briar chat` will use to consume briar's
own server. Asserts the tool catalog, a read tool, and that a gated tool
dry-runs by default and only writes when called with confirm=true.

Skips cleanly when the `mcp` extra isn't installed.
"""

from __future__ import annotations

import json
import sys

import pytest

pytest.importorskip("mcp", reason="requires the `mcp` extra: pip install 'briar-cli[mcp]'")

from briar.iac.runbook.models import McpServerBinding  # noqa: E402
from briar.mcp import McpClientManager  # noqa: E402

pytestmark = pytest.mark.integration


def _binding(root: str) -> McpServerBinding:
    # Launch with THIS interpreter so the subprocess shares our site-packages
    # (the `mcp` SDK). Forward PYTHONPATH by env-var NAME so a `PYTHONPATH=src`
    # parent run reaches the child and `src/briar` shadows any stale install.
    args = ["-m", "briar", "mcp", "serve", "--transport", "stdio", "--store", "file", "--root", root]
    return McpServerBinding(transport="stdio", command=sys.executable, args=args, env={"PYTHONPATH": "PYTHONPATH"})


@pytest.fixture
def manager(tmp_path):
    mgr = McpClientManager({"briar": _binding(str(tmp_path / "kn"))}, connect_timeout_s=60.0)
    try:
        yield mgr
    finally:
        mgr.close()


def test_lists_briar_tools(manager) -> None:
    names = {t.name for t in manager.start()}
    # A representative slice of the catalog, namespaced by the client.
    assert {"mcp__briar__version", "mcp__briar__knowledge_put", "mcp__briar__knowledge_get", "mcp__briar__extract_run"} <= names


def test_version_read_tool(manager) -> None:
    from briar import __version__

    tools = {t.name: t for t in manager.start()}
    assert tools["mcp__briar__version"].run().strip() == __version__


def test_knowledge_put_gated_then_confirmed(manager) -> None:
    tools = {t.name: t for t in manager.start()}
    put = tools["mcp__briar__knowledge_put"]
    get = tools["mcp__briar__knowledge_get"]

    # Default = dry run: reports intent, writes nothing. A missing blob
    # flattens to the SDK's empty-content sentinel.
    preview = json.loads(put.run(blob_name="knowledge:acme", content="hello"))
    assert preview["executed"] is False
    assert "would write" in preview["summary"]
    assert get.run(blob_name="knowledge:acme").strip() == "(empty result)"

    # confirm=true performs the write; the read tool sees it.
    done = json.loads(put.run(blob_name="knowledge:acme", content="hello", confirm=True))
    assert done["executed"] is True
    assert get.run(blob_name="knowledge:acme").strip() == "hello"


def test_chat_session_drives_real_server_with_human_gate(manager, tmp_path) -> None:
    """briar chat's ChatSession, over the REAL server, gated by a fake human.

    A scripted fake LLM (no API key) issues one knowledge_put tool call; the
    human-confirm callback approves it; the blob must land on disk via the
    full chat → manager → server subprocess → service path."""
    from briar.agent._enums import StopReason
    from briar.agent._llm import LLMResponse, LLMToolCall
    from briar.commands.chat import ChatSession

    tools = manager.start()
    by_name = {t.name: t for t in tools}

    script = [
        LLMResponse(
            text="",
            tool_calls=[LLMToolCall(id="c1", name="mcp__briar__knowledge_put", arguments={"blob_name": "knowledge:chat", "content": "via chat"})],
            stop_reason=StopReason.TOOL_USE,
            input_tokens=1,
            output_tokens=1,
        ),
        LLMResponse(text="wrote it", tool_calls=[], stop_reason=StopReason.END_TURN, input_tokens=1, output_tokens=1),
    ]

    class _FakeLLM:
        kind = "fake"

        def __init__(self) -> None:
            self.i = 0

        def complete(self, **_):
            r = script[self.i]
            self.i += 1
            return r

        def format_tool_result(self, *, tool_call_id, output, is_error=False):
            return {"type": "tool_result", "tool_use_id": tool_call_id, "content": output, "is_error": is_error}

    session = ChatSession(_FakeLLM(), tools, confirm_fn=lambda _p: True)
    assert session.ask("write a blob") == "wrote it"
    # The gated write actually happened on the real server.
    assert by_name["mcp__briar__knowledge_get"].run(blob_name="knowledge:chat").strip() == "via chat"
