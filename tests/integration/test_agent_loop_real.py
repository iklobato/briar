"""End-to-end: the REAL AgentRunner tool-use loop driven by the REAL Anthropic
SDK against a wire-level fake of the Messages API.

Only the `/v1/messages` HTTP wire is faked (a real 127.0.0.1 server, seeded with
a SCRIPTED multi-turn sequence). Everything else runs for real:

  * `AnthropicLLM.complete` builds + parses each turn via the SDK's httpx client
    and strict pydantic response model;
  * `AgentRunner.run` walks its loop, dispatches the tool_use block to the REAL
    sandboxed file tools (observable side effect on disk), feeds the tool_result
    back, and stops on `end_turn`;
  * token accounting accumulates across both turns.

Messages API contract + the tool_use / tool_result shapes are modelled on:
  https://docs.anthropic.com/en/api/messages
  https://docs.anthropic.com/en/docs/build-with-claude/tool-use/overview
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from briar.agent.runner import AgentRunConfig, AgentRunner
from briar.storage.file import StoreFile

pytestmark = pytest.mark.integration


def _make_runner(workdir: Path, knowledge_root: Path) -> AgentRunner:
    """Construct the REAL runner against the engineer archetype. The
    AnthropicLLM it builds reads ANTHROPIC_BASE_URL/API_KEY from env (set
    by the `anthropic_at` fixture), so its SDK client hits the mock server."""
    cfg = AgentRunConfig(
        company="acme",
        task="impl",
        archetype_name="engineer",
        workdir=workdir,
        knowledge_store=StoreFile(root=knowledge_root),
        target="acme/widgets",
        max_iterations=5,
    )
    # llm=None → runner builds a real AnthropicLLM(kind="anthropic").
    return AgentRunner(cfg, llm_kind="anthropic")


def test_runner_multi_turn_tool_use_then_end_turn(anthropic_at, tmp_path) -> None:
    """Turn 1: model asks to write a file (tool_use). The runner runs the
    REAL WriteFileTool (file appears on disk). Turn 2: model returns end_turn
    text. Assert the loop fed the tool_result back, stopped, and accounted
    tokens across BOTH turns."""
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    target_file = workdir / "NOTES.md"

    # ── Turn 1: a tool_use block (documented stop_reason="tool_use"). ──
    # write_file is one of the runner's bound tools; running it for real
    # creates the file on disk so the side effect is observable.
    anthropic_at.add(
        "POST",
        "/v1/messages",
        {
            "id": "msg_turn1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [
                {"type": "text", "text": "Writing the notes file."},
                {
                    "type": "tool_use",
                    "id": "toolu_write_01",
                    "name": "write_file",
                    "input": {"path": str(target_file), "content": "hello from the agent loop\n"},
                },
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 40},
        },
    )
    # ── Turn 2: end_turn with final text (documented success body). ──
    anthropic_at.add(
        "POST",
        "/v1/messages",
        {
            "id": "msg_turn2",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": "Done — wrote NOTES.md."}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 130, "output_tokens": 12},
        },
    )

    runner = _make_runner(workdir, knowledge)
    result = runner.run()

    # ── Final behavior: loop stopped on end_turn, surfaced the text. ──
    assert result.error == ""
    assert result.stop_reason == "end_turn"
    assert result.final_text == "Done — wrote NOTES.md."
    assert result.iterations == 2  # one tool_use turn + one end_turn turn
    assert result.tool_calls == 1

    # ── Token accounting accumulates across BOTH turns. ──
    assert result.input_tokens == 100 + 130
    assert result.output_tokens == 40 + 12

    # ── REAL side effect: the sandboxed WriteFileTool wrote the file. ──
    assert target_file.read_text(encoding="utf-8") == "hello from the agent loop\n"

    # ── Exactly two POSTs to /v1/messages (the two scripted turns). ──
    posts = [r for r in anthropic_at.received if r["path"] == "/v1/messages" and r["method"] == "POST"]
    assert len(posts) == 2

    first = json.loads(posts[0]["body"])
    second = json.loads(posts[1]["body"])

    # First request: the model + the bound tools the runner exposes.
    assert first["model"] == "claude-sonnet-4-5"
    assert first["max_tokens"] == AgentRunner.DEFAULT_MAX_TOKENS_PER_TURN
    tool_names = {t["name"] for t in first["tools"]}
    assert {"bash", "read_file", "write_file", "edit_file"} <= tool_names
    # No channels configured → send_message tool is NOT bound.
    assert "send_message" not in tool_names
    # First turn carries only the initial user message.
    assert [m["role"] for m in first["messages"]] == ["user"]

    # ── The CRUX: the second request fed the tool_result back. ──
    # The runner appends (a) the assistant's raw tool_use message and
    # (b) a user message carrying the tool_result block keyed by the
    # tool_use id, per the Messages-API tool-use protocol.
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]
    assistant_echo = second["messages"][1]
    assert any(
        block.get("type") == "tool_use" and block.get("id") == "toolu_write_01" for block in assistant_echo["content"]
    ), "runner did not echo the assistant tool_use message back"

    tool_result_blocks = [b for b in second["messages"][2]["content"] if b.get("type") == "tool_result"]
    assert len(tool_result_blocks) == 1
    tr = tool_result_blocks[0]
    assert tr["tool_use_id"] == "toolu_write_01"
    # The tool_result carries the REAL WriteFileTool output, not a stub.
    assert "wrote 26 bytes" in tr["content"]
    assert str(target_file) in tr["content"]
    assert "is_error" not in tr  # success path → no error flag


def test_runner_feeds_tool_error_back_when_tool_rejects(anthropic_at, tmp_path) -> None:
    """Unhappy path: the model asks to write OUTSIDE the sandbox. The REAL
    WriteFileTool raises ToolError; the runner must feed that back as a
    tool_result with is_error=True (not crash, not silently drop it), then
    the model recovers with end_turn."""
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()

    # /etc is outside every allowed root (workdir, /tmp, /var/lib/briar).
    # Use an absolute escape path so it resolves outside the sandbox on
    # Linux CI too (tmp_path lives under /tmp there).
    escape = "/etc/briar-should-never-write-this"

    anthropic_at.add(
        "POST",
        "/v1/messages",
        {
            "id": "msg_bad",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_bad",
                    "name": "write_file",
                    "input": {"path": escape, "content": "nope"},
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 50, "output_tokens": 20},
        },
    )
    anthropic_at.add(
        "POST",
        "/v1/messages",
        {
            "id": "msg_recover",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [{"type": "text", "text": "Understood — that path is out of bounds."}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 70, "output_tokens": 10},
        },
    )

    runner = _make_runner(workdir, knowledge)
    result = runner.run()

    # Loop survived the tool error and ran to completion.
    assert result.error == ""
    assert result.stop_reason == "end_turn"
    assert result.final_text == "Understood — that path is out of bounds."
    assert result.tool_calls == 1

    # The escape file was NOT created (sandbox held).
    assert not Path(escape).exists()

    # The error was fed back to the model as is_error=True.
    posts = [r for r in anthropic_at.received if r["path"] == "/v1/messages"]
    second = json.loads(posts[1]["body"])
    tr = [b for b in second["messages"][2]["content"] if b.get("type") == "tool_result"][0]
    assert tr["tool_use_id"] == "toolu_bad"
    assert tr["is_error"] is True
    assert "outside allowed roots" in tr["content"]
