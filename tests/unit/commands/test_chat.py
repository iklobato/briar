"""`briar chat` — the ChatSession tool-use loop and its human-in-the-loop gate.

No real LLM or subprocess: a scripted fake LLM emits tool calls, fake tools
return read values or gated dry-run JSON, and a scripted confirm callback
stands in for the human. Asserts the model can't self-confirm and that the
confirm/decline branches re-invoke (or don't) the gated tool.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from briar.agent._enums import StopReason
from briar.agent._llm import LLMResponse, LLMToolCall
from briar.commands.chat import ChatSession


class _FakeLLM:
    """Replays a scripted list of LLMResponses, one per `complete` call."""

    kind = "fake"

    def __init__(self, script: List[LLMResponse]) -> None:
        self._script = list(script)
        self.calls = 0

    def complete(self, **_: Any) -> LLMResponse:
        resp = self._script[self.calls]
        self.calls += 1
        return resp

    def format_tool_result(self, *, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:
        return {"tool_call_id": tool_call_id, "output": output, "is_error": is_error}


class _RecordingTool:
    """A gated tool: dry-run JSON unless called with confirm=True."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "test tool"
        self.INPUT_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}, "confirm": {"type": "boolean"}}}
        self.calls: List[Dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if kwargs.get("confirm"):
            return json.dumps({"mode": "execute", "executed": True, "summary": "did it", "result": {}})
        return json.dumps({"mode": "dry_run", "executed": False, "summary": "would do it", "result": None})


def _tool_use(name: str, args: Dict[str, Any]) -> LLMResponse:
    return LLMResponse(text="", tool_calls=[LLMToolCall(id="c1", name=name, arguments=args)], stop_reason=StopReason.TOOL_USE, input_tokens=1, output_tokens=1)


def _end(text: str) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], stop_reason=StopReason.END_TURN, input_tokens=1, output_tokens=1)


def test_confirm_yes_reinvokes_with_confirm_true() -> None:
    tool = _RecordingTool("mcp__briar__knowledge_put")
    llm = _FakeLLM([_tool_use(tool.name, {"x": "v"}), _end("done")])
    session = ChatSession(llm, [tool], confirm_fn=lambda _p: True)

    assert session.ask("write it") == "done"
    # First call dry-run (no confirm), second with confirm=True after approval.
    assert tool.calls == [{"x": "v"}, {"x": "v", "confirm": True}]


def test_confirm_no_does_not_execute() -> None:
    tool = _RecordingTool("mcp__briar__knowledge_put")
    llm = _FakeLLM([_tool_use(tool.name, {"x": "v"}), _end("ok")])
    session = ChatSession(llm, [tool], confirm_fn=lambda _p: False)

    assert session.ask("write it") == "ok"
    # Only the dry-run call happened; the execute call did not.
    assert tool.calls == [{"x": "v"}]


def test_model_cannot_self_confirm() -> None:
    # Even if the model passes confirm=True, the client strips it and gates.
    tool = _RecordingTool("mcp__briar__knowledge_put")
    llm = _FakeLLM([_tool_use(tool.name, {"x": "v", "confirm": True}), _end("ok")])
    session = ChatSession(llm, [tool], confirm_fn=lambda _p: False)

    session.ask("sneaky")
    # The first (and only) call must NOT carry confirm — it was stripped.
    assert tool.calls == [{"x": "v"}]


def test_read_tool_not_gated() -> None:
    class _Read:
        name = "mcp__briar__version"
        description = "v"
        INPUT_SCHEMA = {"type": "object", "properties": {}}

        def __init__(self) -> None:
            self.calls = 0

        def run(self, **_: Any) -> str:
            self.calls += 1
            return "1.2.3"

    tool = _Read()
    confirms = []
    llm = _FakeLLM([_tool_use(tool.name, {}), _end("the version is 1.2.3")])
    session = ChatSession(llm, [tool], confirm_fn=lambda p: confirms.append(p) or True)

    assert session.ask("version?") == "the version is 1.2.3"
    assert tool.calls == 1
    assert confirms == []  # no gate prompt for a read
