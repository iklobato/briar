"""AWS Bedrock LLM adapter (Converse API) — request construction,
response parsing, and botocore-error propagation.

Response/request shape modelled on the Bedrock Converse API:
https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html
— request: ``modelId``, ``system`` (list of ``{text}`` blocks),
``messages`` (each ``{role, content:[blocks]}``), ``inferenceConfig``
(``maxTokens``), optional ``toolConfig.tools[].toolSpec``. Response:
``output.message.content`` (blocks with ``text`` or ``toolUse``),
``stopReason`` ("end_turn" / "tool_use" / "max_tokens"), ``usage``
(``inputTokens`` / ``outputTokens``).

Errors: botocore raises ``ClientError`` whose
``response['Error']['Code']`` is e.g. ThrottlingException (429-equiv),
ValidationException (400-equiv), AccessDeniedException (403-equiv).
The adapter has no retry policy, so these propagate unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from briar.agent._enums import StopReason
from briar.agent._llms import make_llm
from briar.agent._llms.bedrock import BedrockLLM

pytestmark = pytest.mark.boundary


def _converse_response(blocks: List[Dict[str, Any]], *, stop_reason: str, input_tokens: int = 10, output_tokens: int = 5) -> Dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": blocks}},
        "stopReason": stop_reason,
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
    }


# ── is_available ──────────────────────────────────────────────────────


def test_is_available_true_with_boto3() -> None:
    # boto3 is a hard runtime dep — Bedrock gates only on SDK import, not
    # an env var (auth comes from the ambient AWS credential chain).
    assert BedrockLLM().is_available() is True


def test_is_available_false_when_boto3_missing(mocker: Any) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "boto3":
            raise ImportError("no boto3")
        return real_import(name, *args, **kwargs)

    mocker.patch.object(builtins, "__import__", side_effect=fake_import)
    assert BedrockLLM().is_available() is False


# ── complete(): success / request shape ───────────────────────────────


def test_complete_returns_text_stop_and_usage(fake_bedrock_client: Any) -> None:
    fake_bedrock_client.return_value = _converse_response(
        [{"text": "hello "}, {"text": "world"}],
        stop_reason="endTurn",
        input_tokens=30,
        output_tokens=8,
    )

    resp = make_llm("bedrock").complete(system="", messages=[{"role": "user", "content": "hi"}], tools=[], max_tokens=64)

    assert resp.text == "hello world"
    assert resp.tool_calls == []
    assert resp.stop_reason == StopReason.END_TURN
    assert resp.input_tokens == 30
    assert resp.output_tokens == 8
    assert resp.raw_assistant_message == {"role": "assistant", "content": [{"text": "hello "}, {"text": "world"}]}


def test_complete_builds_converse_request(fake_bedrock_client: Any) -> None:
    fake_bedrock_client.return_value = _converse_response([{"text": "ok"}], stop_reason="endTurn")

    tools = [{"name": "grep", "description": "search", "input_schema": {"type": "object"}}]
    make_llm("bedrock", model="anthropic.claude-3-5-sonnet-20240620-v1:0").complete(
        system="be terse",
        messages=[{"role": "user", "content": "go"}],
        tools=tools,
        max_tokens=512,
    )

    kwargs = fake_bedrock_client.call_args.kwargs
    assert kwargs["modelId"] == "anthropic.claude-3-5-sonnet-20240620-v1:0"
    # system → list of {text} blocks.
    assert kwargs["system"] == [{"text": "be terse"}]
    # plain-text user message → {role, content:[{text}]}.
    assert kwargs["messages"] == [{"role": "user", "content": [{"text": "go"}]}]
    assert kwargs["inferenceConfig"] == {"maxTokens": 512}
    # Anthropic-shaped tool → Bedrock toolSpec with inputSchema.json.
    assert kwargs["toolConfig"] == {
        "tools": [
            {
                "toolSpec": {
                    "name": "grep",
                    "description": "search",
                    "inputSchema": {"json": {"type": "object"}},
                }
            }
        ]
    }


def test_empty_system_sends_empty_list_and_no_toolconfig(fake_bedrock_client: Any) -> None:
    fake_bedrock_client.return_value = _converse_response([{"text": "ok"}], stop_reason="endTurn")
    make_llm("bedrock").complete(system="", messages=[], tools=[], max_tokens=10)
    kwargs = fake_bedrock_client.call_args.kwargs
    assert kwargs["system"] == []
    assert "toolConfig" not in kwargs


def test_default_model(fake_bedrock_client: Any) -> None:
    fake_bedrock_client.return_value = _converse_response([{"text": "ok"}], stop_reason="endTurn")
    make_llm("bedrock").complete(system="", messages=[], tools=[], max_tokens=10)
    assert fake_bedrock_client.call_args.kwargs["modelId"] == "anthropic.claude-sonnet-4-20250514-v1:0"


def test_client_is_built_once_and_reused(mocker: Any) -> None:
    converse = mocker.MagicMock(return_value=_converse_response([{"text": "ok"}], stop_reason="endTurn"))
    client = mocker.MagicMock(converse=converse)
    ctor = mocker.patch("boto3.client", return_value=client)

    llm = make_llm("bedrock")
    llm.complete(system="", messages=[], tools=[], max_tokens=10)
    llm.complete(system="", messages=[], tools=[], max_tokens=10)

    ctor.assert_called_once_with("bedrock-runtime")


def test_structured_message_passed_through_untouched(fake_bedrock_client: Any) -> None:
    # A message whose content is already a list of blocks (e.g. a prior
    # tool_result) must pass through unchanged, not be re-wrapped.
    fake_bedrock_client.return_value = _converse_response([{"text": "ok"}], stop_reason="endTurn")
    tool_result_msg = {"role": "user", "content": [{"toolResult": {"toolUseId": "t1", "content": [{"text": "out"}]}}]}
    make_llm("bedrock").complete(system="", messages=[tool_result_msg], tools=[], max_tokens=10)
    assert fake_bedrock_client.call_args.kwargs["messages"] == [tool_result_msg]


# ── tool-use round trip ───────────────────────────────────────────────


def test_complete_extracts_tool_use_block(fake_bedrock_client: Any) -> None:
    fake_bedrock_client.return_value = _converse_response(
        [
            {"text": "searching"},
            {"toolUse": {"toolUseId": "tooluse_1", "name": "grep", "input": {"pattern": "TODO"}}},
        ],
        stop_reason="toolUse",
    )

    resp = make_llm("bedrock").complete(system="", messages=[], tools=[], max_tokens=10)

    assert resp.text == "searching"
    assert resp.stop_reason == StopReason.TOOL_USE
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.id == "tooluse_1"
    assert call.name == "grep"
    assert call.arguments == {"pattern": "TODO"}


def test_unknown_block_kind_is_ignored(fake_bedrock_client: Any) -> None:
    # A content block that is neither {text} nor {toolUse} (e.g. a future
    # reasoning/guardrail block) is skipped without crashing.
    fake_bedrock_client.return_value = _converse_response(
        [{"reasoningContent": {"text": "internal"}}, {"text": "visible"}],
        stop_reason="endTurn",
    )
    resp = make_llm("bedrock").complete(system="", messages=[], tools=[], max_tokens=10)
    assert resp.text == "visible"
    assert resp.tool_calls == []


def test_unknown_stop_reason_passes_through_raw(fake_bedrock_client: Any) -> None:
    # max_tokens isn't mapped to a StopReason enum → raw string survives.
    fake_bedrock_client.return_value = _converse_response([{"text": "trunc"}], stop_reason="max_tokens")
    resp = make_llm("bedrock").complete(system="", messages=[], tools=[], max_tokens=10)
    assert resp.stop_reason == "max_tokens"


def test_empty_output_message_yields_blank_response(fake_bedrock_client: Any) -> None:
    # Defensive: a malformed/empty output dict must not KeyError.
    fake_bedrock_client.return_value = {"stopReason": "endTurn", "usage": {}}
    resp = make_llm("bedrock").complete(system="", messages=[], tools=[], max_tokens=10)
    assert resp.text == ""
    assert resp.tool_calls == []
    assert resp.input_tokens == 0
    assert resp.output_tokens == 0


def test_format_tool_result_error_status(fake_bedrock_client: Any) -> None:
    llm = make_llm("bedrock")
    ok = llm.format_tool_result(tool_call_id="t1", output="done")
    err = llm.format_tool_result(tool_call_id="t2", output="boom", is_error=True)
    assert ok == {"role": "user", "content": [{"toolResult": {"toolUseId": "t1", "content": [{"text": "done"}]}}]}
    assert err["content"][0]["toolResult"]["status"] == "error"
    assert "status" not in ok["content"][0]["toolResult"]


# ── failure modes (botocore ClientError — propagate raw) ──────────────


def test_throttling_exception_propagates(fake_bedrock_client: Any, botocore_client_error: Any) -> None:
    from botocore.exceptions import ClientError

    fake_bedrock_client.side_effect = botocore_client_error("ThrottlingException", message="Rate exceeded")
    with pytest.raises(ClientError) as ctx:
        make_llm("bedrock").complete(system="", messages=[], tools=[], max_tokens=10)
    assert ctx.value.response["Error"]["Code"] == "ThrottlingException"
    # No retry policy → exactly one converse call.
    assert fake_bedrock_client.call_count == 1


def test_validation_exception_propagates(fake_bedrock_client: Any, botocore_client_error: Any) -> None:
    # ValidationException is Bedrock's context-length / bad-input signal.
    from botocore.exceptions import ClientError

    fake_bedrock_client.side_effect = botocore_client_error("ValidationException", message="too many tokens")
    with pytest.raises(ClientError) as ctx:
        make_llm("bedrock").complete(system="", messages=[], tools=[], max_tokens=10)
    assert ctx.value.response["Error"]["Code"] == "ValidationException"


def test_access_denied_exception_propagates(fake_bedrock_client: Any, botocore_client_error: Any) -> None:
    from botocore.exceptions import ClientError

    fake_bedrock_client.side_effect = botocore_client_error("AccessDeniedException", message="not authorized")
    with pytest.raises(ClientError) as ctx:
        make_llm("bedrock").complete(system="", messages=[], tools=[], max_tokens=10)
    assert ctx.value.response["Error"]["Code"] == "AccessDeniedException"
