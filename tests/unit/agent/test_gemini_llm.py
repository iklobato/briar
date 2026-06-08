"""Google Gemini LLM adapter — request construction, response parsing,
and SDK-error propagation.

Response shape modelled on generateContent:
https://ai.google.dev/api/generate-content#GenerateContentResponse —
``candidates[0].content.parts[]`` each carry either ``text`` or a
``function_call`` (``{name, args}``); ``candidates[0].finish_reason`` is
an enum (STOP / MAX_TOKENS / SAFETY / RECITATION / OTHER); token counts
live in ``usage_metadata`` (prompt_token_count / candidates_token_count).

Gemini has no native tool-call id, so the adapter uses the function
*name* as the id. The adapter has no retry policy, so SDK errors
propagate unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

# `google-generativeai` is the optional `[gemini]` extra; CI installs only
# `[test]`, so skip this whole module when the SDK is absent rather than
# erroring at fixture setup. (When present, conftest.py pre-imports it with the
# one-shot deprecation FutureWarning suppressed so filterwarnings=["error"]
# doesn't turn an SDK-level deprecation into a spurious failure here.)
pytest.importorskip("google.generativeai")

from briar.agent._enums import StopReason  # noqa: E402
from briar.agent._llms import make_llm  # noqa: E402
from briar.agent._llms.gemini import GeminiLLM  # noqa: E402

pytestmark = pytest.mark.boundary


def _text_part(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, function_call=None, to_dict=lambda: {"text": text})


def _function_call_part(*, name: str, args: dict) -> SimpleNamespace:
    fc = SimpleNamespace(name=name, args=args)
    return SimpleNamespace(text=None, function_call=fc, to_dict=lambda: {"function_call": {"name": name, "args": args}})


def _finish_reason(name: str) -> SimpleNamespace:
    # google.generativeai exposes finish_reason as an enum whose `.name` is e.g. "STOP".
    return SimpleNamespace(name=name)


def _response(
    parts: List[Any],
    *,
    finish_reason: str = "STOP",
    prompt_tokens: int = 8,
    candidate_tokens: int = 4,
    candidates: Optional[List[Any]] = None,
) -> SimpleNamespace:
    if candidates is None:
        candidate = SimpleNamespace(content=SimpleNamespace(parts=parts), finish_reason=_finish_reason(finish_reason))
        candidates = [candidate]
    usage = SimpleNamespace(prompt_token_count=prompt_tokens, candidates_token_count=candidate_tokens)
    return SimpleNamespace(candidates=candidates, usage_metadata=usage)


# ── is_available ──────────────────────────────────────────────────────


def test_is_available_true_with_key_and_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    assert GeminiLLM().is_available() is True


def test_is_available_false_without_key() -> None:
    assert GeminiLLM().is_available() is False


def test_is_available_false_when_sdk_missing(monkeypatch: pytest.MonkeyPatch, mocker: Any) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    mocker.patch("briar.agent._llms.gemini._import_genai", return_value=None)
    assert GeminiLLM().is_available() is False


def test_required_env_vars() -> None:
    assert GeminiLLM.required_env_vars() == ["GEMINI_API_KEY"]


def test_import_genai_returns_none_when_module_absent(mocker: Any) -> None:
    # Exercise the real lazy-import seam: ImportError → None.
    from briar.agent._llms import gemini

    mocker.patch.object(gemini.importlib, "import_module", side_effect=ImportError("no genai"))
    assert gemini._import_genai() is None


# ── complete(): success / request shape ───────────────────────────────


def test_complete_returns_text_stop_and_usage(monkeypatch: pytest.MonkeyPatch, fake_gemini_model: Any) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    fake_gemini_model.return_value = _response([_text_part("hello "), _text_part("there")], finish_reason="STOP", prompt_tokens=20, candidate_tokens=6)

    resp = make_llm("gemini").complete(system="", messages=[{"role": "user", "content": "hi"}], tools=[], max_tokens=128)

    assert resp.text == "hello there"
    assert resp.tool_calls == []
    assert resp.stop_reason == StopReason.END_TURN
    assert resp.input_tokens == 20
    assert resp.output_tokens == 6
    assert resp.raw_assistant_message == {"role": "model", "parts": [{"text": "hello "}, {"text": "there"}]}


def test_complete_configures_model_and_generation_config(monkeypatch: pytest.MonkeyPatch, mocker: Any, fake_gemini_model: Any) -> None:
    import google.generativeai as genai

    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    configure = mocker.patch.object(genai, "configure")
    ctor = mocker.patch.object(genai, "GenerativeModel")
    ctor.return_value.generate_content.return_value = _response([_text_part("ok")])

    tools = [{"name": "grep", "description": "search", "input_schema": {"type": "object"}}]
    make_llm("gemini", model="gemini-2.5-pro").complete(system="be terse", messages=[{"role": "user", "content": "go"}], tools=tools, max_tokens=222)

    configure.assert_called_once_with(api_key="g-x")
    # Model is constructed with the system instruction + translated tools.
    model_kwargs = ctor.call_args.kwargs
    assert model_kwargs["model_name"] == "gemini-2.5-pro"
    assert model_kwargs["system_instruction"] == "be terse"
    assert model_kwargs["tools"] == [{"function_declarations": [{"name": "grep", "description": "search", "parameters": {"type": "object"}}]}]
    # generate_content receives translated contents + max_output_tokens.
    gen_kwargs = ctor.return_value.generate_content.call_args.kwargs
    assert gen_kwargs["contents"] == [{"role": "user", "parts": [{"text": "go"}]}]
    assert gen_kwargs["generation_config"] == {"max_output_tokens": 222}


def test_assistant_role_maps_to_model(monkeypatch: pytest.MonkeyPatch, fake_gemini_model: Any, mocker: Any) -> None:
    import google.generativeai as genai

    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    ctor = mocker.patch.object(genai, "GenerativeModel")
    ctor.return_value.generate_content.return_value = _response([_text_part("ok")])

    make_llm("gemini").complete(
        system="",
        messages=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
        tools=[],
        max_tokens=10,
    )
    contents = ctor.return_value.generate_content.call_args.kwargs["contents"]
    # Anthropic role "assistant" → Gemini role "model".
    assert contents[0]["role"] == "user"
    assert contents[1]["role"] == "model"


def test_structured_content_parts_passed_through(monkeypatch: pytest.MonkeyPatch, fake_gemini_model: Any, mocker: Any) -> None:
    # A message whose content is already a list of parts (e.g. a prior
    # function_response from format_tool_result) is forwarded as-is, not
    # re-wrapped into {"text": ...}.
    import google.generativeai as genai

    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    ctor = mocker.patch.object(genai, "GenerativeModel")
    ctor.return_value.generate_content.return_value = _response([_text_part("ok")])

    parts = [{"function_response": {"name": "grep", "response": {"content": "out"}}}]
    make_llm("gemini").complete(system="", messages=[{"role": "user", "content": parts}], tools=[], max_tokens=10)

    contents = ctor.return_value.generate_content.call_args.kwargs["contents"]
    assert contents == [{"role": "user", "parts": parts}]


def test_empty_candidates_returns_blank_response(monkeypatch: pytest.MonkeyPatch, fake_gemini_model: Any) -> None:
    # SAFETY-blocked / empty completion → candidates=[]; adapter must not IndexError.
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    fake_gemini_model.return_value = _response([], candidates=[])
    resp = make_llm("gemini").complete(system="", messages=[], tools=[], max_tokens=10)
    assert resp.text == ""
    assert resp.tool_calls == []
    assert resp.stop_reason == ""
    assert resp.input_tokens == 0


def test_non_stop_finish_reason_lowercased(monkeypatch: pytest.MonkeyPatch, fake_gemini_model: Any) -> None:
    # MAX_TOKENS (truncation) isn't END_TURN → exposed as lowercase string.
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    fake_gemini_model.return_value = _response([_text_part("trunc")], finish_reason="MAX_TOKENS")
    resp = make_llm("gemini").complete(system="", messages=[], tools=[], max_tokens=10)
    assert resp.stop_reason == "max_tokens"


# ── tool-use round trip ───────────────────────────────────────────────


def test_complete_extracts_function_call(monkeypatch: pytest.MonkeyPatch, fake_gemini_model: Any) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    fake_gemini_model.return_value = _response(
        [_text_part("calling tool"), _function_call_part(name="grep", args={"pattern": "TODO"})],
        finish_reason="STOP",
    )

    resp = make_llm("gemini").complete(system="", messages=[], tools=[], max_tokens=10)

    assert resp.text == "calling tool"
    # Presence of a tool call overrides finish_reason → TOOL_USE.
    assert resp.stop_reason == StopReason.TOOL_USE
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    # Gemini has no native id — name doubles as id.
    assert call.id == "grep"
    assert call.name == "grep"
    assert call.arguments == {"pattern": "TODO"}


def test_format_tool_result_correlates_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = make_llm("gemini")
    msg = llm.format_tool_result(tool_call_id="grep", output="found 3", is_error=False)
    assert msg["role"] == "user"
    fr = msg["parts"][0]["function_response"]
    assert fr["name"] == "grep"  # correlation key is the function name, not an id
    assert fr["response"] == {"content": "found 3", "is_error": False}


# ── failure modes (propagate raw — no retry policy) ───────────────────


def test_missing_sdk_raises_install_hint(monkeypatch: pytest.MonkeyPatch, mocker: Any) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    mocker.patch("briar.agent._llms.gemini._import_genai", return_value=None)
    with pytest.raises(RuntimeError, match=r"briar-cli\[gemini\]"):
        make_llm("gemini").complete(system="", messages=[], tools=[], max_tokens=10)


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # SDK present (installed) but no key → clear RuntimeError.
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        make_llm("gemini").complete(system="", messages=[], tools=[], max_tokens=10)


def test_generate_content_error_propagates(monkeypatch: pytest.MonkeyPatch, fake_gemini_model: Any) -> None:
    # Gemini surfaces auth/quota failures as google.api_core exceptions; the
    # adapter wraps nothing, so whatever the SDK raises reaches the caller.
    # https://ai.google.dev/gemini-api/docs/troubleshooting — 429 RESOURCE_EXHAUSTED.
    from google.api_core import exceptions as gax

    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    fake_gemini_model.side_effect = gax.ResourceExhausted("429 quota exceeded")
    with pytest.raises(gax.ResourceExhausted):
        make_llm("gemini").complete(system="", messages=[], tools=[], max_tokens=10)
    assert fake_gemini_model.call_count == 1
