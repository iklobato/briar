"""Anthropic LLM adapter — request construction, response parsing, and
the SDK-exception → retry/abort policy.

Response shape modelled on the Messages API:
https://docs.anthropic.com/en/api/messages — the response carries
``content`` (a list of blocks, each ``type`` of "text" or "tool_use"),
``stop_reason``, and ``usage`` (input_tokens / output_tokens).

Errors modelled on https://docs.anthropic.com/en/api/errors — the SDK
raises ``anthropic.APIStatusError`` subclasses whose ``.status_code``
mirrors the HTTP status (authentication_error 401, rate_limit_error 429,
overloaded_error 529, invalid_request_error 400).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List

import anthropic
import pytest

from briar.agent._llms import make_llm
from briar.agent._llms.anthropic_llm import AnthropicLLM

pytestmark = pytest.mark.boundary


def _text_block(text: str) -> SimpleNamespace:
    # https://docs.anthropic.com/en/api/messages — a {"type":"text","text":...} block.
    return SimpleNamespace(type="text", text=text, model_dump=lambda: {"type": "text", "text": text})


def _tool_use_block(*, id: str, name: str, input: dict) -> SimpleNamespace:
    # https://docs.anthropic.com/en/api/messages — {"type":"tool_use","id","name","input"}.
    return SimpleNamespace(
        type="tool_use",
        id=id,
        name=name,
        input=input,
        model_dump=lambda: {"type": "tool_use", "id": id, "name": name, "input": input},
    )


def _message(content: List[Any], *, stop_reason: str, input_tokens: int = 11, output_tokens: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


# ── is_available ──────────────────────────────────────────────────────


def test_is_available_true_with_oauth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok_xxx")
    assert AnthropicLLM().is_available() is True


def test_is_available_true_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    assert AnthropicLLM().is_available() is True


def test_is_available_false_without_any_credential() -> None:
    # env_sandbox autouse fixture already stripped ANTHROPIC_/CLAUDE_CODE_ vars.
    assert AnthropicLLM().is_available() is False


def test_required_env_vars_lists_both_auth_routes() -> None:
    assert AnthropicLLM.required_env_vars() == ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"]


# ── complete(): success / request shape ───────────────────────────────


def test_complete_returns_parsed_text_and_usage(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    fake_anthropic_messages.create.return_value = _message(
        [_text_block("hello "), _text_block("world")],
        stop_reason="end_turn",
        input_tokens=42,
        output_tokens=9,
    )

    llm = make_llm("anthropic", model="claude-test")
    resp = llm.complete(system="be terse", messages=[{"role": "user", "content": "hi"}], tools=[], max_tokens=256)

    # Parsed VALUES, not "the mock was called".
    assert resp.text == "hello world"
    assert resp.tool_calls == []
    assert resp.stop_reason == "end_turn"
    assert resp.input_tokens == 42
    assert resp.output_tokens == 9
    assert resp.raw_assistant_message == {
        "role": "assistant",
        "content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}],
    }


def test_complete_builds_request_with_passed_args(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    fake_anthropic_messages.create.return_value = _message([_text_block("ok")], stop_reason="end_turn")

    tools = [{"name": "grep", "description": "search", "input_schema": {"type": "object"}}]
    messages = [{"role": "user", "content": "go"}]
    llm = make_llm("anthropic", model="claude-sonnet-4-5")
    llm.complete(system="SYS", messages=messages, tools=tools, max_tokens=1234)

    kwargs = fake_anthropic_messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-5"
    assert kwargs["max_tokens"] == 1234
    assert kwargs["system"] == "SYS"
    assert kwargs["tools"] == tools  # Anthropic tools passed through untranslated
    assert kwargs["messages"] == messages


def test_default_model_used_when_unspecified(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    fake_anthropic_messages.create.return_value = _message([_text_block("ok")], stop_reason="end_turn")

    make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=10)
    assert fake_anthropic_messages.create.call_args.kwargs["model"] == "claude-sonnet-4-5"


def test_oauth_token_builds_client_with_beta_header(monkeypatch: pytest.MonkeyPatch, mocker: Any) -> None:
    # OAuth subscription billing requires the oauth-2025-04-20 beta header.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok")
    ctor = mocker.patch("anthropic.Anthropic")
    ctor.return_value.messages.create.return_value = _message([_text_block("ok")], stop_reason="end_turn")

    make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=10)

    kwargs = ctor.call_args.kwargs
    assert kwargs["auth_token"] == "oauth-tok"
    assert kwargs["default_headers"] == {"anthropic-beta": "oauth-2025-04-20"}
    assert "api_key" not in kwargs


def test_client_is_built_once_and_reused(monkeypatch: pytest.MonkeyPatch, mocker: Any) -> None:
    # One client per provider instance across multiple .complete() calls —
    # this is what keeps the executor's retry budget coherent for one task.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    ctor = mocker.patch("anthropic.Anthropic")
    ctor.return_value.messages.create.return_value = _message([_text_block("ok")], stop_reason="end_turn")

    llm = make_llm("anthropic")
    llm.complete(system="", messages=[], tools=[], max_tokens=10)
    llm.complete(system="", messages=[], tools=[], max_tokens=10)

    assert ctor.call_count == 1


def test_build_client_raises_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = make_llm("anthropic")
    with pytest.raises(RuntimeError, match="CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY"):
        llm.complete(system="", messages=[], tools=[], max_tokens=10)


# ── tool-use round trip ───────────────────────────────────────────────


def test_complete_extracts_tool_use_block(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    fake_anthropic_messages.create.return_value = _message(
        [
            _text_block("let me search"),
            _tool_use_block(id="toolu_01", name="grep", input={"pattern": "TODO"}),
        ],
        stop_reason="tool_use",
    )

    resp = make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=64)

    assert resp.text == "let me search"
    assert resp.stop_reason == "tool_use"
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.id == "toolu_01"
    assert call.name == "grep"
    assert call.arguments == {"pattern": "TODO"}


def test_tool_use_with_empty_input_normalises_to_dict(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any) -> None:
    # `input` may be None/empty; adapter coerces to {} so callers never see None.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    fake_anthropic_messages.create.return_value = _message(
        [_tool_use_block(id="toolu_x", name="noop", input=None)],
        stop_reason="tool_use",
    )
    resp = make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=8)
    assert resp.tool_calls[0].arguments == {}


def test_unknown_block_type_is_ignored(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any) -> None:
    # A block whose type is neither text nor tool_use (e.g. a future
    # "thinking" block) is skipped, not crashed on.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    unknown = SimpleNamespace(type="thinking", model_dump=lambda: {"type": "thinking"})
    fake_anthropic_messages.create.return_value = _message(
        [unknown, _text_block("visible")],
        stop_reason="end_turn",
    )
    resp = make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=8)
    assert resp.text == "visible"
    assert resp.tool_calls == []


def test_format_tool_result_marks_error_flag() -> None:
    llm = make_llm("anthropic")
    ok = llm.format_tool_result(tool_call_id="t1", output="done")
    err = llm.format_tool_result(tool_call_id="t2", output="boom", is_error=True)
    assert ok == {"type": "tool_result", "tool_use_id": "t1", "content": "done"}
    assert err["is_error"] is True
    assert "is_error" not in ok  # success path must NOT set the flag


# ── failure modes (error-policy registry) ─────────────────────────────


def test_rate_limit_429_aborts_without_retry(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any, anthropic_error: Any, mocker: Any) -> None:
    """429 RateLimitError → Abort: the adapter must propagate immediately,
    NOT sleep/retry (the whole point of the policy change)."""
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    sleep = mocker.patch("briar.error_policy.time.sleep")
    fake_anthropic_messages.create.side_effect = anthropic_error(429, cls=anthropic.RateLimitError)

    with pytest.raises(anthropic.RateLimitError):
        make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=8)

    # Abort: exactly one attempt, no sleeping.
    assert fake_anthropic_messages.create.call_count == 1
    sleep.assert_not_called()


def test_auth_401_aborts(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any, anthropic_error: Any, mocker: Any) -> None:
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    sleep = mocker.patch("briar.error_policy.time.sleep")
    fake_anthropic_messages.create.side_effect = anthropic_error(401, cls=anthropic.AuthenticationError)

    with pytest.raises(anthropic.AuthenticationError):
        make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=8)
    assert fake_anthropic_messages.create.call_count == 1
    sleep.assert_not_called()


def test_forbidden_403_aborts(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any, anthropic_error: Any, mocker: Any) -> None:
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    mocker.patch("briar.error_policy.time.sleep")
    # PermissionDeniedError is the 403 subclass; status_code carries 403.
    fake_anthropic_messages.create.side_effect = anthropic_error(403, cls=anthropic.PermissionDeniedError)
    with pytest.raises(anthropic.PermissionDeniedError):
        make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=8)
    assert fake_anthropic_messages.create.call_count == 1


def test_overloaded_529_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any, anthropic_error: Any, mocker: Any) -> None:
    """529 overloaded_error → RetryAfter(120). Adapter retries (without
    really sleeping) and returns the eventual success."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    sleep = mocker.patch("briar.error_policy.time.sleep")
    fake_anthropic_messages.create.side_effect = [
        anthropic_error(529, message="overloaded"),
        _message([_text_block("recovered")], stop_reason="end_turn"),
    ]

    resp = make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=8)

    assert resp.text == "recovered"
    assert fake_anthropic_messages.create.call_count == 2
    sleep.assert_called_once_with(120)


def test_service_unavailable_503_retries(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any, anthropic_error: Any, mocker: Any) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    sleep = mocker.patch("briar.error_policy.time.sleep")
    fake_anthropic_messages.create.side_effect = [
        anthropic_error(503, message="unavailable"),
        _message([_text_block("ok")], stop_reason="end_turn"),
    ]
    resp = make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=8)
    assert resp.text == "ok"
    sleep.assert_called_once_with(30)


def test_connection_error_retries(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any, mocker: Any) -> None:
    import anthropic
    import httpx

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    sleep = mocker.patch("briar.error_policy.time.sleep")
    conn_err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    fake_anthropic_messages.create.side_effect = [conn_err, _message([_text_block("ok")], stop_reason="end_turn")]
    resp = make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=8)
    assert resp.text == "ok"
    sleep.assert_called_once_with(10)


def test_context_length_400_propagates_unretried(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any, anthropic_error: Any, mocker: Any) -> None:
    """invalid_request_error (400) has no explicit policy → the tail
    _PROPAGATE null-object Aborts. One attempt, raised, no sleep."""
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    sleep = mocker.patch("briar.error_policy.time.sleep")
    fake_anthropic_messages.create.side_effect = anthropic_error(400, cls=anthropic.BadRequestError, message="prompt is too long")
    with pytest.raises(anthropic.BadRequestError):
        make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=8)
    assert fake_anthropic_messages.create.call_count == 1
    sleep.assert_not_called()


def test_retry_budget_exhausts_after_max_attempts(monkeypatch: pytest.MonkeyPatch, fake_anthropic_messages: Any, anthropic_error: Any, mocker: Any) -> None:
    """A persistently-503 endpoint retries up to max_attempts (5) then
    re-raises the last exception — it must not loop forever."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    mocker.patch("briar.error_policy.time.sleep")
    fake_anthropic_messages.create.side_effect = anthropic_error(503, message="still down")
    with pytest.raises(anthropic.APIStatusError):
        make_llm("anthropic").complete(system="", messages=[], tools=[], max_tokens=8)
    assert fake_anthropic_messages.create.call_count == 5
