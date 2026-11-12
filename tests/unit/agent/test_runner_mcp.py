"""Runner-level wiring tests for MCP tools.

Assert the observable wiring without the `mcp` SDK or an event loop: a
fake `McpClientManager` (monkeypatched onto `briar.mcp`) hands the runner
a fake MCP tool. We then assert the runner (a) binds it only when an
`mcp:` block is present, (b) advertises it in `_tool_specs()`, (c)
dispatches a tool_use call to it and feeds the result back, and (d)
closes the manager on every exit path.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest import mock

from briar.agent._enums import StopReason
from briar.agent._llm import LLMProvider, LLMResponse, LLMToolCall
from briar.agent.runner import AgentRunConfig, AgentRunner
from briar.iac.scaffold.archetypes import ARCHETYPES

_ENGINEER = ARCHETYPES["engineer"].name


class ScriptedLLM(LLMProvider):
    kind = "scripted"

    def __init__(self, turns: List[LLMResponse]) -> None:
        self._turns = list(turns)
        self._i = 0
        self.seen_tools: List[List[Dict[str, Any]]] = []
        self.seen_messages: List[List[Dict[str, Any]]] = []
        self.seen_systems: List[str] = []

    def is_available(self) -> bool:
        return True

    def complete(self, *, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], max_tokens: int) -> LLMResponse:
        self.seen_tools.append(tools)
        self.seen_messages.append([dict(m) for m in messages])
        self.seen_systems.append(system)
        turn = self._turns[self._i]
        self._i += 1
        return turn

    def format_tool_result(self, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:
        block: Dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_call_id, "content": output}
        if is_error:
            block["is_error"] = True
        return block


class _FakeMcpTool:
    name = "mcp__github__search_issues"
    description = "Search GitHub issues."
    server = "github"
    purpose = "GitHub issues and pull requests"
    INPUT_SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}}

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "found 3 issues"


class FakeManager:
    """Stand-in for McpClientManager: records lifecycle, hands back one tool."""

    instances: List["FakeManager"] = []

    def __init__(self, bindings: Any, **_: Any) -> None:
        self.bindings = bindings
        self.started = False
        self.closed = False
        self.tool = _FakeMcpTool()
        FakeManager.instances.append(self)

    def start(self) -> List[_FakeMcpTool]:
        self.started = True
        return [self.tool]

    def close(self) -> None:
        self.closed = True


def _text_turn(text: str, *, in_tok: int = 0, out_tok: int = 0) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], stop_reason=StopReason.END_TURN, input_tokens=in_tok, output_tokens=out_tok)


def _tool_turn(call: LLMToolCall) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[call],
        stop_reason=StopReason.TOOL_USE,
        input_tokens=0,
        output_tokens=0,
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


def test_no_mcp_block_binds_no_mcp_tools(tmp_path: Path) -> None:
    llm = ScriptedLLM([_text_turn("done")])
    runner = _runner(tmp_path, llm)
    assert runner._mcp is None
    assert runner._mcp_tools == []
    names = {t["name"] for t in runner._tool_specs()}
    assert not any(n.startswith("mcp__") for n in names)


def test_mcp_block_binds_and_advertises_tool(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    llm = ScriptedLLM([_text_turn("done")])
    runner = _runner(tmp_path, llm, mcp_servers={"github": object()})
    # Connection is deferred to run(), not the constructor.
    assert runner._mcp is None

    runner.run()

    assert FakeManager.instances[0].started is True
    # The single-turn loop saw the MCP tool advertised in its tool specs.
    assert any(t["name"] == "mcp__github__search_issues" for t in llm.seen_tools[0])


def test_mcp_tool_is_dispatched_and_result_fed_back(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    call = LLMToolCall(id="tc1", name="mcp__github__search_issues", arguments={"query": "bug"})
    llm = ScriptedLLM([_tool_turn(call), _text_turn("all done")])
    runner = _runner(tmp_path, llm, mcp_servers={"github": object()})

    result = runner.run()

    assert result.final_text == "all done"
    assert result.tool_calls == 1
    # The fake MCP tool received the kwargs the LLM passed.
    assert FakeManager.instances[0].tool.calls == [{"query": "bug"}]
    # And its output was fed back as the next turn's tool_result.
    fed_back = llm.seen_messages[1][-1]["content"]
    assert fed_back[0]["tool_use_id"] == "tc1"
    assert fed_back[0]["content"] == "found 3 issues"
    assert "is_error" not in fed_back[0]


def test_manager_closed_after_run(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    llm = ScriptedLLM([_text_turn("done")])
    runner = _runner(tmp_path, llm, mcp_servers={"github": object()})

    runner.run()

    assert FakeManager.instances[0].closed is True


def test_context_source_section_reaches_the_model(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    llm = ScriptedLLM([_text_turn("done")])
    runner = _runner(tmp_path, llm, mcp_servers={"github": object()})

    runner.run()

    system = llm.seen_systems[0]  # the system prompt the model actually received
    assert "## Context sources" in system
    # The server's purpose + its tool namespace steer the routing judgment.
    assert "GitHub issues and pull requests" in system
    assert "mcp__github__*" in system
    # Local repo is always the first, most-preferred source.
    assert "Local repository" in system


def test_no_context_source_section_without_mcp(tmp_path: Path) -> None:
    llm = ScriptedLLM([_text_turn("done")])
    runner = _runner(tmp_path, llm)

    runner.run()

    assert "Context sources" not in llm.seen_systems[0]


def test_server_scoped_to_other_archetype_is_not_bound(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    llm = ScriptedLLM([_text_turn("done")])
    # engineer run, server restricted to pr-fixer → scoped out entirely.
    runner = _runner(tmp_path, llm, mcp_servers={"github": SimpleNamespace(archetypes=["pr-fixer"])})

    runner.run()

    assert runner._mcp is None
    assert runner._mcp_tools == []
    assert FakeManager.instances == []  # manager never even constructed
    assert not any(t["name"].startswith("mcp__") for t in llm.seen_tools[0])


def test_server_scoped_to_this_archetype_is_bound(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    llm = ScriptedLLM([_text_turn("done")])
    runner = _runner(tmp_path, llm, mcp_servers={"github": SimpleNamespace(archetypes=[_ENGINEER])})

    runner.run()

    assert runner._mcp is not None
    assert "mcp__github__search_issues" in {t.name for t in runner._mcp_tools}


def test_empty_archetypes_binds_for_every_archetype(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    llm = ScriptedLLM([_text_turn("done")])
    binding = SimpleNamespace(archetypes=[])
    runner = _runner(tmp_path, llm, mcp_servers={"github": binding})

    runner.run()

    assert runner._mcp is not None
    assert FakeManager.instances[0].bindings == {"github": binding}


def test_manager_closed_even_on_dry_run(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    llm = ScriptedLLM([])  # never called on dry run
    runner = _runner(tmp_path, llm, mcp_servers={"github": object()}, dry_run=True)

    result = runner.run()

    assert result.stop_reason == StopReason.DRY_RUN
    assert FakeManager.instances[0].closed is True


# ── Lever 4: the always-on router pre-pass ──────────────────────────────


def _two_servers() -> Dict[str, Any]:
    return {
        "github": SimpleNamespace(archetypes=[], purpose="issues and PRs"),
        "sentry": SimpleNamespace(archetypes=[], purpose="production errors"),
    }


def test_router_connects_only_selected_servers(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    # turn 0 = router reply (picks github); turn 1 = the loop's end_turn.
    llm = ScriptedLLM([_text_turn('["github"]'), _text_turn("done")])
    runner = _runner(tmp_path, llm, mcp_servers=_two_servers())

    runner.run()

    # Manager was built with ONLY the routed subset.
    assert set(FakeManager.instances[0].bindings) == {"github"}


def test_router_fails_open_on_unparseable_reply(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    llm = ScriptedLLM([_text_turn("I cannot decide"), _text_turn("done")])
    runner = _runner(tmp_path, llm, mcp_servers=_two_servers())

    runner.run()

    # No recognisable selection → connect ALL scoped servers, never strand.
    assert set(FakeManager.instances[0].bindings) == {"github", "sentry"}


def test_router_skipped_for_single_server(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    llm = ScriptedLLM([_text_turn("done")])  # only the loop turn — no router call
    runner = _runner(tmp_path, llm, mcp_servers={"github": SimpleNamespace(archetypes=[], purpose="x")})

    runner.run()

    # Routing among one server is pointless, so the LLM saw exactly one
    # call (the loop), not a wasted router pre-pass.
    assert len(llm.seen_messages) == 1
    assert set(FakeManager.instances[0].bindings) == {"github"}


def test_router_tokens_folded_into_result(tmp_path: Path, monkeypatch) -> None:
    FakeManager.instances.clear()
    monkeypatch.setattr("briar.mcp.McpClientManager", FakeManager)
    llm = ScriptedLLM([_text_turn('["github"]', in_tok=5, out_tok=2), _text_turn("done", in_tok=10, out_tok=3)])
    runner = _runner(tmp_path, llm, mcp_servers=_two_servers())

    result = runner.run()

    # Router (5/2) + loop (10/3) both counted — the cost summary tells truth.
    assert result.input_tokens == 15
    assert result.output_tokens == 5
