"""Behavioural tests for the `AgentRunner.run` tool-use loop.

Drives the loop with a scripted in-process `LLMProvider` (no SDK, no
network) implementing the real two-verb contract from
``briar.agent._llm.LLMProvider``: ``complete`` (one turn) +
``format_tool_result`` (echo-back shape). Each test scripts a sequence
of turns and asserts the *observable* loop behaviour:

  - a tool_use turn executes the tool, feeds the result back as the
    next user message, and the loop continues;
  - an end_turn surfaces the final text and stops;
  - the iteration ceiling is enforced exactly;
  - an LLM exception is caught and surfaced as `result.error`;
  - an unexpected stop_reason stops with an error;
  - token usage accumulates across turns;
  - the bash commit-hook records SHAs from real bash stdout.

These assert the values the runner *produced* (final_text, error,
iterations, tokens, commits, the message list the provider saw) — not
that a mock was called — so a flipped stop-condition or off-by-one in
the iteration cap makes a test FAIL.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pytest

from briar.agent._enums import StopReason
from briar.agent._llm import LLMProvider, LLMResponse, LLMToolCall
from briar.agent.runner import AgentRunConfig, AgentRunner


class ScriptedLLM(LLMProvider):
    """An `LLMProvider` that replays a fixed list of `LLMResponse`s.

    Records every ``complete`` call's ``messages`` argument so a test can
    assert the runner fed tool results back in. ``format_tool_result``
    mirrors the Anthropic shape (the runner is vendor-agnostic; any
    dict-with-content works)."""

    kind = "scripted"

    def __init__(self, turns: List[LLMResponse]) -> None:
        self._turns = list(turns)
        self._i = 0
        self.seen_messages: List[List[Dict[str, Any]]] = []

    def is_available(self) -> bool:
        return True

    def complete(self, *, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], max_tokens: int) -> LLMResponse:
        # Deep-ish snapshot of what the runner handed us this turn.
        self.seen_messages.append([dict(m) for m in messages])
        turn = self._turns[self._i]
        self._i += 1
        return turn

    def format_tool_result(self, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:
        block: Dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_call_id, "content": output}
        if is_error:
            block["is_error"] = True
        return block


class RaisingLLM(LLMProvider):
    kind = "raising"

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def is_available(self) -> bool:
        return True

    def complete(self, **_: Any) -> LLMResponse:  # type: ignore[override]
        raise self._exc

    def format_tool_result(self, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:  # pragma: no cover
        return {}


def _text_turn(text: str, *, in_tok: int = 0, out_tok: int = 0) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], stop_reason=StopReason.END_TURN, input_tokens=in_tok, output_tokens=out_tok)


def _tool_turn(call: LLMToolCall, *, in_tok: int = 0, out_tok: int = 0) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[call],
        stop_reason=StopReason.TOOL_USE,
        input_tokens=in_tok,
        output_tokens=out_tok,
        raw_assistant_message={"role": "assistant", "content": [{"type": "tool_use", "id": call.id}]},
    )


def _runner(tmp_path: Path, llm: LLMProvider, **cfg: Any) -> AgentRunner:
    store = mock.MagicMock()
    store.list.return_value = []
    store.get.return_value = ""
    config = AgentRunConfig(
        company="acme",
        task="implement",
        archetype_name="engineer",
        workdir=tmp_path,
        knowledge_store=store,
        target="acme/widgets",
        **cfg,
    )
    return AgentRunner(config, llm=llm)


# ── happy loop: tool_use → result fed back → end_turn ──────────────────


def test_loop_executes_tool_then_stops_on_end_turn(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("FILE BODY", encoding="utf-8")
    call = LLMToolCall(id="tc1", name="read_file", arguments={"path": str(tmp_path / "f.txt")})
    llm = ScriptedLLM([_tool_turn(call), _text_turn("all done")])
    runner = _runner(tmp_path, llm)

    result = runner.run()

    assert result.final_text == "all done"
    assert result.stop_reason == StopReason.END_TURN
    assert result.iterations == 2
    assert result.tool_calls == 1
    assert result.error == ""
    # The second turn must have seen the tool result fed back as a user
    # message containing the file body the tool actually read.
    second_turn_msgs = llm.seen_messages[1]
    assert second_turn_msgs[-1]["role"] == "user"
    fed_back = second_turn_msgs[-1]["content"]
    assert fed_back[0]["type"] == "tool_result"
    assert fed_back[0]["tool_use_id"] == "tc1"
    assert fed_back[0]["content"] == "FILE BODY"
    assert "is_error" not in fed_back[0]


def test_loop_feeds_tool_error_back_with_is_error(tmp_path: Path) -> None:
    # read a missing file → ToolError → fed back with is_error True, loop continues.
    call = LLMToolCall(id="tcX", name="read_file", arguments={"path": str(tmp_path / "missing.txt")})
    llm = ScriptedLLM([_tool_turn(call), _text_turn("recovered")])
    runner = _runner(tmp_path, llm)

    result = runner.run()

    assert result.final_text == "recovered"
    fed_back = llm.seen_messages[1][-1]["content"][0]
    assert fed_back["is_error"] is True
    assert "read_file" in fed_back["content"]


def test_loop_immediate_end_turn_no_tools(tmp_path: Path) -> None:
    llm = ScriptedLLM([_text_turn("nothing to do", in_tok=5, out_tok=3)])
    result = _runner(tmp_path, llm).run()
    assert result.iterations == 1
    assert result.tool_calls == 0
    assert result.final_text == "nothing to do"


# ── token accounting accumulates across turns ──────────────────────────


def test_token_usage_accumulates(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("y", encoding="utf-8")
    call = LLMToolCall(id="t", name="read_file", arguments={"path": str(tmp_path / "x")})
    llm = ScriptedLLM([_tool_turn(call, in_tok=10, out_tok=4), _text_turn("done", in_tok=7, out_tok=2)])
    result = _runner(tmp_path, llm).run()
    assert result.input_tokens == 17
    assert result.output_tokens == 6


# ── iteration ceiling ──────────────────────────────────────────────────


def test_iteration_ceiling_enforced(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("y", encoding="utf-8")
    call = LLMToolCall(id="t", name="read_file", arguments={"path": str(tmp_path / "x")})
    # Always returns tool_use → never ends → must stop at the cap.
    llm = ScriptedLLM([_tool_turn(call) for _ in range(10)])
    result = _runner(tmp_path, llm, max_iterations=3).run()
    assert result.iterations == 3
    assert "iteration ceiling (3)" in result.error
    assert result.final_text == ""
    # complete() was called exactly max_iterations times, not more.
    assert len(llm.seen_messages) == 3


def test_zero_max_iterations_falls_back_to_default(tmp_path: Path) -> None:
    # config.max_iterations falsy → DEFAULT_MAX_ITERATIONS (30) used.
    llm = ScriptedLLM([_text_turn("ok")])
    runner = _runner(tmp_path, llm, max_iterations=0)
    assert runner._max_iterations == AgentRunner.DEFAULT_MAX_ITERATIONS
    assert runner.run().final_text == "ok"


# ── error / unhappy paths ──────────────────────────────────────────────


def test_llm_exception_surfaces_as_error(tmp_path: Path) -> None:
    result = _runner(tmp_path, RaisingLLM(RuntimeError("network blip"))).run()
    assert result.error == "api: LLM call failed (see traceback in log)"
    assert result.iterations == 1
    assert result.final_text == ""


def test_unexpected_stop_reason_stops_with_error(tmp_path: Path) -> None:
    weird = LLMResponse(text="", tool_calls=[], stop_reason="max_tokens", input_tokens=1, output_tokens=1)
    result = _runner(tmp_path, ScriptedLLM([weird])).run()
    assert result.error == "unexpected stop_reason=max_tokens"
    assert result.iterations == 1


def test_unknown_tool_name_is_fed_back_as_error_then_loop_continues(tmp_path: Path) -> None:
    call = LLMToolCall(id="z", name="frobnicate", arguments={})
    llm = ScriptedLLM([_tool_turn(call), _text_turn("gave up on that")])
    result = _runner(tmp_path, llm).run()
    assert result.final_text == "gave up on that"
    fed_back = llm.seen_messages[1][-1]["content"][0]
    assert fed_back["is_error"] is True
    assert "unknown tool 'frobnicate'" in fed_back["content"]


def test_missing_llm_credentials_short_circuits_with_actionable_error(tmp_path: Path) -> None:
    llm = mock.MagicMock(spec=LLMProvider)
    llm.kind = "anthropic"
    llm.is_available.return_value = False
    type(llm).required_env_vars = mock.MagicMock(return_value=["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"])
    result = _runner(tmp_path, llm).run()
    assert "credentials missing" in result.error
    assert "CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY" in result.error
    # No turn was ever attempted.
    llm.complete.assert_not_called()
    assert result.iterations == 0


# ── dry run: no LLM call at all ────────────────────────────────────────


def test_dry_run_skips_llm_entirely(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    llm = mock.MagicMock(spec=LLMProvider)
    llm.kind = "anthropic"
    runner = _runner(tmp_path, llm, dry_run=True)
    result = runner.run()
    assert result.stop_reason == StopReason.DRY_RUN
    assert result.final_text == "(dry run — no LLM call)"
    assert result.iterations == 0
    llm.complete.assert_not_called()
    llm.is_available.assert_not_called()
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "SYSTEM PROMPT" in out
    assert "TOOLS BOUND" in out


# ── commit hook records SHAs from real bash output ─────────────────────


def test_commit_sha_recorded_from_bash_stdout(tmp_path: Path) -> None:
    # The runner's commit-hook fires only for the bash tool: it scrapes the
    # `[branch sha] subject` line git prints on commit. Drive a real bash
    # turn but stub subprocess.run so the test owns the stdout.
    call = LLMToolCall(id="b1", name="bash", arguments={"command": "git commit -m x", "cwd": str(tmp_path)})
    llm = ScriptedLLM([_tool_turn(call), _text_turn("committed")])
    runner = _runner(tmp_path, llm)
    completed = mock.MagicMock(returncode=0, stdout="[main 1a2b3c4] do the thing\n 1 file changed", stderr="")
    with mock.patch("briar.agent.tools.subprocess.run", return_value=completed):
        result = runner.run()
    assert result.final_text == "committed"
    assert result.commits == ["1a2b3c4"]


class _Section:
    def __init__(self, title: str, body: str, is_empty: bool = False) -> None:
        self.title = title
        self.body = body
        self.is_empty = is_empty


def test_system_prompt_includes_nonempty_sections_skips_empty(tmp_path: Path) -> None:
    llm = ScriptedLLM([_text_turn("ok")])
    runner = _runner(
        tmp_path,
        llm,
        task_context_sections=(
            _Section("Ticket Context", "PROJ-42 body"),
            _Section("Hidden", "should not appear", is_empty=True),
        ),
    )
    prompt = runner._build_system_prompt()
    assert "## Ticket Context" in prompt
    assert "PROJ-42 body" in prompt
    assert "Hidden" not in prompt


def test_initial_user_message_appends_extra_instructions(tmp_path: Path) -> None:
    runner = _runner(tmp_path, ScriptedLLM([_text_turn("ok")]), extra_user_instructions="Be terse.")
    msg = runner._build_initial_user_message()
    assert "Additional instructions:\nBe terse." in msg


def test_system_prompt_survives_knowledge_splicer_failure(tmp_path: Path) -> None:
    # If the splicer blows up, the runner logs and continues with no
    # prologue rather than aborting the run.
    runner = _runner(tmp_path, ScriptedLLM([_text_turn("ok")]))
    with mock.patch(
        "briar.iac.scaffold._knowledge.KnowledgeSplicer.from_store",
        side_effect=RuntimeError("store down"),
    ):
        prompt = runner._build_system_prompt()
    assert "Working directory:" in prompt  # body still rendered


def test_dry_run_lists_send_channels_and_sections(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    llm = mock.MagicMock(spec=LLMProvider)
    llm.kind = "anthropic"
    runner = _runner(
        tmp_path,
        llm,
        dry_run=True,
        messages={"ops": {"kind": "slack-channel"}},
        task_context_sections=(_Section("Ticket Context", "PROJ-1 body"),),
    )
    runner.run()
    out = capsys.readouterr().out
    # send_message tool spec rendered with its available-channels suffix.
    assert "send_message" in out
    assert "Available channels: ops" in out
    # task-scoped section listed with its byte count.
    assert "Ticket Context" in out
    assert "bytes)" in out


def test_system_prompt_includes_prologue(tmp_path: Path) -> None:
    # When the splicer returns a non-empty prologue it is appended as the
    # first section ahead of the task-scoped ones.
    runner = _runner(tmp_path, ScriptedLLM([_text_turn("ok")]))
    splicer = mock.MagicMock()
    splicer.prologue.return_value = "KNOWLEDGE PROLOGUE"
    with mock.patch(
        "briar.iac.scaffold._knowledge.KnowledgeSplicer.from_store",
        return_value=splicer,
    ):
        prompt = runner._build_system_prompt()
    assert "KNOWLEDGE PROLOGUE" in prompt


def test_bash_output_without_commit_line_records_nothing(tmp_path: Path) -> None:
    # Non-commit bash stdout must not spuriously append a SHA.
    from briar.agent.runner import AgentRunResult

    AgentRunner._record_commit_if_any("On branch main\nnothing to commit", res := AgentRunResult(company="c", task="t"))
    assert res.commits == []


def test_cost_summary_pricing() -> None:
    from briar.agent.runner import AgentRunResult

    r = AgentRunResult(company="c", task="t", input_tokens=1_000_000, output_tokens=1_000_000)
    # $3/M in + $15/M out = $18.000
    assert r.cost_summary() == "in=1,000,000 out=1,000,000 ≈$18.000"
