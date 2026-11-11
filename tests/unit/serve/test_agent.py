"""The agent of `briar serve` (spec 003 PR 2: S1, S23, S25): the Agent SDK options it
builds, the answer it reads, and the guard of each tool call. The SDK `query` is replaced;
no model is called."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ResultError, ResultMessage

from briar.serve.agent import Access, AgentRequest, BashGuard, ClaudeAgent, ToolGate

MAX_USD = 2.5
COST = 0.42


class SdkQuery:
    """Stands in for `claude_agent_sdk.query`: records the options, yields one result."""

    def __init__(self, message: ResultMessage) -> None:
        self.options: List[Any] = []
        self._message = message

    async def query(self, prompt: str, options: Any) -> AsyncIterator[Any]:
        self.options.append(options)
        yield self._message


class FailingQuery:
    """Stands in for `claude_agent_sdk.query` on a failed run: the error result, then the raise."""

    def __init__(self, message: ResultMessage, error: Exception) -> None:
        self._message = message
        self._error = error

    async def query(self, prompt: str, options: Any) -> AsyncIterator[Any]:
        yield self._message
        raise self._error


class Results:
    def message(self, subtype: str, structured: Dict[str, Any]) -> ResultMessage:
        return ResultMessage(
            subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=subtype != "success", num_turns=1, session_id="s",
            total_cost_usd=COST, result="the plan", structured_output=structured,
        )  # fmt: skip


@pytest.fixture
def guard(tmp_path: Path) -> BashGuard:
    return BashGuard(tmp_path / "worktree", [])


class TestClaudeAgent:
    def test_builds_the_options_and_reads_the_answer(self, guard: BashGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sdk = SdkQuery(Results().message("success", {"summary": "s", "steps": []}))
        monkeypatch.setattr("claude_agent_sdk.query", sdk.query)
        request = AgentRequest("plan it", "you are briar", tmp_path / "worktree", MAX_USD, {"type": "object"}, Access.READ)

        answer = asyncio.run(ClaudeAgent("claude-test", guard).ask(request))

        options = sdk.options[0]
        expected = ("claude-test", str(tmp_path / "worktree"), "you are briar", MAX_USD, "default", [])
        assert (options.model, options.cwd, options.system_prompt, options.max_budget_usd, options.permission_mode, options.setting_sources) == expected
        assert (options.output_format, isinstance(options.can_use_tool, ToolGate)) == ({"type": "json_schema", "schema": {"type": "object"}}, True)
        assert (answer.text, answer.structured, answer.cost_usd, answer.failed) == ("the plan", {"summary": "s", "steps": []}, COST, False)

    def test_a_spent_budget_is_a_failed_answer_with_its_reason(self, guard: BashGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        message = Results().message("error_max_budget_usd", {})
        error = ResultError("Claude Code returned an error result: budget", data={"subtype": "error_max_budget_usd"})
        monkeypatch.setattr("claude_agent_sdk.query", FailingQuery(message, error).query)

        answer = asyncio.run(ClaudeAgent("claude-test", guard).ask(AgentRequest("build", "s", tmp_path, MAX_USD)))

        assert (answer.failed, answer.reason, answer.cost_usd) == (True, "error_max_budget_usd", COST)

    def test_an_api_error_keeps_the_text_of_the_sdk(self, guard: BashGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The real SDK, with a refused key: subtype `success`, the cause in the message (2026-11-11)."""
        text = "Claude Code returned an error result: Invalid API key · Fix external API key"
        error = ResultError(text, data={"subtype": "success", "total_cost_usd": 0})
        monkeypatch.setattr("claude_agent_sdk.query", FailingQuery(Results().message("success", {}), error).query)

        answer = asyncio.run(ClaudeAgent("claude-test", guard).ask(AgentRequest("plan", "s", tmp_path, MAX_USD)))

        assert (answer.failed, answer.reason) == (True, text)


class TestBashGuard:
    @pytest.mark.parametrize(
        "command, refusal",
        [
            ("pytest -q", ""),
            ("git status && make test", ""),
            ('echo "a; b" | grep a', ""),
            ("git push origin main", "forbidden in briar serve: 'git push' (push, PR and comments are done by briar)"),
            ("curl https://example.test", "forbidden in briar serve: 'curl' (push, PR and comments are done by briar)"),
            ("rm -rf build", "forbidden in briar serve: 'rm -rf' (push, PR and comments are done by briar)"),
            ("docker ps", "not in the bash allowlist of this company: 'docker'"),
            ("ls && docker ps", "not in the bash allowlist of this company: 'docker'"),
        ],
    )
    def test_bash_runs_only_allowed_programs(self, guard: BashGuard, command: str, refusal: str) -> None:
        assert guard.refusal("Bash", {"command": command}) == refusal

    def test_the_company_allowlist_replaces_the_default(self, tmp_path: Path) -> None:
        guard = BashGuard(tmp_path, ["docker"])

        assert (guard.refusal("Bash", {"command": "docker ps"}), guard.refusal("Bash", {"command": "pytest"})) == (
            "",
            "not in the bash allowlist of this company: 'pytest'",
        )

    def test_edits_stay_in_the_worktree(self, guard: BashGuard, tmp_path: Path) -> None:
        inside = guard.refusal("Edit", {"file_path": str(tmp_path / "worktree" / "src" / "a.py")})
        outside = guard.refusal("Write", {"file_path": str(tmp_path / "secrets.env")})

        assert (inside, outside) == ("", f"edits stay inside the worktree {tmp_path / 'worktree'}: {tmp_path / 'secrets.env'} is outside")

    def test_reads_pass_and_other_tools_do_not(self, guard: BashGuard) -> None:
        assert (guard.refusal("Read", {"file_path": "/etc/hosts"}), guard.refusal("WebFetch", {})) == ("", "WebFetch is not allowed in briar serve")


class TestToolGate:
    def test_a_read_only_step_cannot_edit(self, guard: BashGuard, tmp_path: Path) -> None:
        edit = {"file_path": str(tmp_path / "worktree" / "a.py")}

        assert (ToolGate(guard, Access.READ).refusal("Edit", edit), ToolGate(guard, Access.EDIT).refusal("Edit", edit)) == (
            "this step only reads: write the answer, do not edit files",
            "",
        )

    def test_the_sdk_gets_allow_or_deny(self, guard: BashGuard) -> None:
        gate = ToolGate(guard, Access.EDIT)

        allowed = asyncio.run(gate("Bash", {"command": "pytest"}, None))
        denied = asyncio.run(gate("Bash", {"command": "curl x"}, None))

        assert (type(allowed), type(denied), denied.message) == (
            PermissionResultAllow,
            PermissionResultDeny,
            "forbidden in briar serve: 'curl' (push, PR and comments are done by briar)",
        )
