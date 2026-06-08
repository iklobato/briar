"""End-to-end: the REAL Anthropic SDK (its httpx client + strict pydantic
response model) talks to a wire-level mock of the Messages API, so the adapter's
request construction and response parsing run for real.

Messages API: https://docs.anthropic.com/en/api/messages
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def test_anthropic_adapter_real_sdk_roundtrip(anthropic_at) -> None:
    from briar.agent._llms.anthropic_llm import AnthropicLLM

    # Documented Messages-API success body — the real SDK validates this shape.
    anthropic_at.add(
        "POST",
        "/v1/messages",
        {
            "id": "msg_PLACEHOLDER",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "Hello from the wire"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 11, "output_tokens": 7},
        },
    )

    llm = AnthropicLLM()
    out = llm.complete(system="be terse", messages=[{"role": "user", "content": "hi"}], tools=[], max_tokens=64)

    # Real SDK parsed the wire response into the adapter's LLMResponse.
    assert out.text == "Hello from the wire"
    assert out.stop_reason == "end_turn"
    assert out.input_tokens == 11
    assert out.output_tokens == 7
    # The adapter really POSTed a well-formed request to /v1/messages.
    posts = [r for r in anthropic_at.received if r["path"] == "/v1/messages"]
    assert posts, "adapter never called /v1/messages"
    sent = json.loads(posts[0]["body"])
    assert sent["system"] == "be terse"
    assert sent["max_tokens"] == 64
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_adapter_real_tool_use_roundtrip(anthropic_at) -> None:
    from briar.agent._llms.anthropic_llm import AnthropicLLM

    anthropic_at.add(
        "POST",
        "/v1/messages",
        {
            "id": "msg_tooluse",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [
                {"type": "text", "text": "I'll read the file."},
                {"type": "tool_use", "id": "toolu_01", "name": "read_file", "input": {"path": "README.md"}},
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 20, "output_tokens": 15},
        },
    )

    llm = AnthropicLLM()
    out = llm.complete(system="", messages=[{"role": "user", "content": "read it"}], tools=[], max_tokens=128)

    assert out.stop_reason == "tool_use"
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "read_file"
    assert out.tool_calls[0].arguments == {"path": "README.md"}
