"""OpenAI LLM adapter — request construction, response parsing, and
SDK-error propagation.

Response shape modelled on Chat Completions:
https://platform.openai.com/docs/api-reference/chat/object — the
response carries ``choices[0].message`` (with ``content`` and optional
``tool_calls`` whose ``function.arguments`` is a JSON *string*),
``choices[0].finish_reason`` ("stop" / "tool_calls" / "length"), and
``usage`` (prompt_tokens / completion_tokens).

Errors: https://platform.openai.com/docs/guides/error-codes — the SDK
raises ``openai.AuthenticationError`` (401), ``RateLimitError`` (429),
``BadRequestError`` (400, e.g. context_length_exceeded),
``APITimeoutError``. The adapter has NO retry policy of its own, so
these propagate unchanged out of ``complete()``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

# `openai` is the optional `[openai]` extra; CI installs only `[test]`, so skip
# this whole module when the SDK is absent rather than erroring on collection.
openai = pytest.importorskip("openai")

from briar.agent._enums import StopReason  # noqa: E402
from briar.agent._llms import make_llm  # noqa: E402
from briar.agent._llms.openai_llm import OpenAILLM  # noqa: E402

pytestmark = pytest.mark.boundary


def _message(content: Optional[str], *, tool_calls: Optional[List[Any]] = None) -> SimpleNamespace:
    dump = {"role": "assistant", "content": content}
    return SimpleNamespace(content=content, tool_calls=tool_calls, model_dump=lambda: dump)


def _completion(message: SimpleNamespace, *, finish_reason: str, prompt_tokens: int = 5, completion_tokens: int = 3) -> SimpleNamespace:
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


def _tool_call(*, id: str, name: str, arguments: str) -> SimpleNamespace:
    # arguments is a JSON STRING per the API contract.
    return SimpleNamespace(id=id, function=SimpleNamespace(name=name, arguments=arguments))


# ── is_available ──────────────────────────────────────────────────────


def test_is_available_true_with_key_and_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert OpenAILLM().is_available() is True


def test_is_available_false_without_key() -> None:
    assert OpenAILLM().is_available() is False


def test_is_available_false_when_sdk_missing(monkeypatch: pytest.MonkeyPatch, mocker: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    mocker.patch("briar.agent._llms.openai_llm._import_openai", return_value=None)
    assert OpenAILLM().is_available() is False


def test_required_env_vars() -> None:
    assert OpenAILLM.required_env_vars() == ["OPENAI_API_KEY"]


def test_import_openai_returns_none_when_module_absent(mocker: Any) -> None:
    # Exercise the real lazy-import seam: ImportError → None (so the opt-in
    # extra stays optional). Patches importlib, not the helper, to pin the
    # actual except branch.
    from briar.agent._llms import openai_llm

    mocker.patch.object(openai_llm.importlib, "import_module", side_effect=ImportError("no openai"))
    assert openai_llm._import_openai() is None


# ── complete(): success / request shape ───────────────────────────────


def test_complete_returns_text_stop_and_usage(monkeypatch: pytest.MonkeyPatch, fake_openai_client: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    fake_openai_client.return_value = _completion(_message("answer text"), finish_reason="stop", prompt_tokens=12, completion_tokens=4)

    resp = make_llm("openai", model="gpt-4o").complete(system="", messages=[{"role": "user", "content": "q"}], tools=[], max_tokens=99)

    assert resp.text == "answer text"
    assert resp.tool_calls == []
    assert resp.stop_reason == StopReason.END_TURN
    assert resp.input_tokens == 12
    assert resp.output_tokens == 4
    assert resp.raw_assistant_message == {"role": "assistant", "content": "answer text"}


def test_complete_prepends_system_and_translates_tools(monkeypatch: pytest.MonkeyPatch, fake_openai_client: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    fake_openai_client.return_value = _completion(_message("ok"), finish_reason="stop")

    tools = [{"name": "grep", "description": "search files", "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}]
    messages = [{"role": "user", "content": "hi"}]
    make_llm("openai", model="gpt-4o-mini").complete(system="be terse", messages=messages, tools=tools, max_tokens=321)

    kwargs = fake_openai_client.call_args.kwargs
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["max_tokens"] == 321
    # system is hoisted into the first message, then the user messages follow.
    assert kwargs["messages"][0] == {"role": "system", "content": "be terse"}
    assert kwargs["messages"][1] == {"role": "user", "content": "hi"}
    # Anthropic-shaped tool → OpenAI function shape (input_schema → parameters).
    assert kwargs["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": "search files",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }
    ]


def test_complete_omits_tools_key_when_none(monkeypatch: pytest.MonkeyPatch, fake_openai_client: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    fake_openai_client.return_value = _completion(_message("ok"), finish_reason="stop")
    make_llm("openai").complete(system="", messages=[], tools=[], max_tokens=10)
    # No system → no system message prepended either.
    assert "tools" not in fake_openai_client.call_args.kwargs
    assert fake_openai_client.call_args.kwargs["messages"] == []


def test_client_is_built_once_and_reused(monkeypatch: pytest.MonkeyPatch, mocker: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    create = mocker.MagicMock(return_value=_completion(_message("ok"), finish_reason="stop"))
    client = mocker.MagicMock()
    client.chat.completions.create = create
    ctor = mocker.patch("openai.OpenAI", return_value=client)

    llm = make_llm("openai")
    llm.complete(system="", messages=[], tools=[], max_tokens=10)
    llm.complete(system="", messages=[], tools=[], max_tokens=10)

    ctor.assert_called_once_with(api_key="sk-x")


def test_default_model_is_gpt4o(monkeypatch: pytest.MonkeyPatch, fake_openai_client: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    fake_openai_client.return_value = _completion(_message("ok"), finish_reason="stop")
    make_llm("openai").complete(system="", messages=[], tools=[], max_tokens=10)
    assert fake_openai_client.call_args.kwargs["model"] == "gpt-4o"


# ── tool-use round trip ───────────────────────────────────────────────


def test_complete_parses_tool_calls_and_json_arguments(monkeypatch: pytest.MonkeyPatch, fake_openai_client: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    fake_openai_client.return_value = _completion(
        _message(None, tool_calls=[_tool_call(id="call_1", name="grep", arguments='{"pattern": "TODO", "n": 3}')]),
        finish_reason="tool_calls",
    )

    resp = make_llm("openai").complete(system="", messages=[], tools=[], max_tokens=10)

    assert resp.text == ""  # None content coerced to ""
    assert resp.stop_reason == StopReason.TOOL_USE
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert call.id == "call_1"
    assert call.name == "grep"
    # JSON-string arguments are parsed into a dict for the caller.
    assert call.arguments == {"pattern": "TODO", "n": 3}


def test_malformed_tool_arguments_degrade_to_empty_dict(monkeypatch: pytest.MonkeyPatch, fake_openai_client: Any) -> None:
    # The model can emit invalid JSON in `arguments`; adapter must not crash.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    fake_openai_client.return_value = _completion(
        _message(None, tool_calls=[_tool_call(id="call_2", name="x", arguments="{not valid json")]),
        finish_reason="tool_calls",
    )
    resp = make_llm("openai").complete(system="", messages=[], tools=[], max_tokens=10)
    assert resp.tool_calls[0].arguments == {}


def test_unknown_finish_reason_passes_through_raw(monkeypatch: pytest.MonkeyPatch, fake_openai_client: Any) -> None:
    # "length" (context truncation) isn't in the mapped vocabulary → raw string.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    fake_openai_client.return_value = _completion(_message("partial"), finish_reason="length")
    resp = make_llm("openai").complete(system="", messages=[], tools=[], max_tokens=10)
    assert resp.stop_reason == "length"


def test_format_tool_result_shape_and_error_prefix() -> None:
    llm = make_llm("openai")
    ok = llm.format_tool_result(tool_call_id="call_1", output="done")
    err = llm.format_tool_result(tool_call_id="call_2", output="boom", is_error=True)
    assert ok == {"role": "tool", "tool_call_id": "call_1", "content": "done"}
    # No native error flag — signalled by content prefix.
    assert err["content"] == "[ERROR] boom"


# ── failure modes (propagate raw — no retry policy) ───────────────────


def test_missing_sdk_raises_install_hint(monkeypatch: pytest.MonkeyPatch, mocker: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    mocker.patch("briar.agent._llms.openai_llm._import_openai", return_value=None)
    with pytest.raises(RuntimeError, match=r"briar-cli\[openai\]"):
        make_llm("openai").complete(system="", messages=[], tools=[], max_tokens=10)


def test_missing_key_raises_at_build_client(monkeypatch: pytest.MonkeyPatch, mocker: Any) -> None:
    # SDK present but no key → clear RuntimeError, not a silent empty response.
    mocker.patch("briar.agent._llms.openai_llm._import_openai", return_value=openai)
    llm = OpenAILLM()
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm.complete(system="", messages=[], tools=[], max_tokens=10)


def test_auth_error_401_propagates(monkeypatch: pytest.MonkeyPatch, fake_openai_client: Any, openai_error: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bad")
    fake_openai_client.side_effect = openai_error(401, cls=openai.AuthenticationError)
    with pytest.raises(openai.AuthenticationError):
        make_llm("openai").complete(system="", messages=[], tools=[], max_tokens=10)


def test_rate_limit_429_propagates(monkeypatch: pytest.MonkeyPatch, fake_openai_client: Any, openai_error: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    fake_openai_client.side_effect = openai_error(429, cls=openai.RateLimitError)
    with pytest.raises(openai.RateLimitError):
        make_llm("openai").complete(system="", messages=[], tools=[], max_tokens=10)
    # No retry → exactly one call.
    assert fake_openai_client.call_count == 1


def test_context_length_400_propagates(monkeypatch: pytest.MonkeyPatch, fake_openai_client: Any, openai_error: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    fake_openai_client.side_effect = openai_error(400, cls=openai.BadRequestError, message="maximum context length")
    with pytest.raises(openai.BadRequestError):
        make_llm("openai").complete(system="", messages=[], tools=[], max_tokens=10)


def test_timeout_propagates(monkeypatch: pytest.MonkeyPatch, fake_openai_client: Any, openai_error: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    fake_openai_client.side_effect = openai_error(0, cls=openai.APITimeoutError)
    with pytest.raises(openai.APITimeoutError):
        make_llm("openai").complete(system="", messages=[], tools=[], max_tokens=10)
