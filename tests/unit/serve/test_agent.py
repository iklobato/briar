"""The agent of `briar serve` (spec 003 PR 2: S1, S23, S25; PR 3: V12, V13): the Agent
SDK options it builds (the sandbox too), the answer it reads, and the guard of each tool
call. The SDK `query` is replaced; no model is called."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ResultError, ResultMessage

from briar.serve.agent import Access, AgentRequest, BashGuard, ClaudeAgent, SandboxRoom, ToolGate

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


@dataclass(frozen=True)
class ReadCase:
    """One read tool call, and the path the guard names when it refuses it."""

    tool: str
    tool_input: Dict[str, str]
    outside: str = ""

    def filled(self, tmp_path: Path) -> Dict[str, str]:
        return {key: value.replace("{knowledge}", str(tmp_path / "knowledge")) for key, value in self.tool_input.items()}


@dataclass(frozen=True)
class ToolsCase:
    """The access of one agent call, and the tools the CLI may offer it."""

    access: Access
    tools: List[str]


@pytest.fixture
def guard(tmp_path: Path) -> BashGuard:
    return BashGuard(tmp_path / "worktree", [], [tmp_path / "knowledge"])


class TestClaudeAgent:
    @pytest.mark.parametrize(
        "case",
        [
            ToolsCase(Access.READ, ["Read", "Glob", "Grep", "LS", "TodoWrite", "Bash"]),
            ToolsCase(Access.EDIT, ["Read", "Glob", "Grep", "LS", "TodoWrite", "Bash", "Edit", "MultiEdit", "Write", "NotebookEdit"]),
        ],
        ids=["read", "edit"],
    )
    def test_the_cli_offers_only_the_tools_the_call_may_use(self, case: ToolsCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Validation run, 2026-11-12: with no tool list the CLI sent its 27 tools, the skill and
        agent lists and the MCP servers of the claude.ai account (Drive, Fireflies...), about 39k
        input tokens per call, though `ToolGate` refuses all but a few of them."""
        sdk = SdkQuery(Results().message("success", {}))
        monkeypatch.setattr("claude_agent_sdk.query", sdk.query)
        agent = ClaudeAgent("claude-test", BashGuard(tmp_path, [], []), SandboxRoom((), tmp_path / "tools"))

        asyncio.run(agent.ask(AgentRequest("plan it", "s", tmp_path, MAX_USD, {}, case.access)))

        assert (sdk.options[0].tools, sdk.options[0].strict_mcp_config) == (case.tools, True)

    def test_builds_the_options_and_reads_the_answer(self, guard: BashGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sdk = SdkQuery(Results().message("success", {"summary": "s", "steps": []}))
        monkeypatch.setattr("claude_agent_sdk.query", sdk.query)
        request = AgentRequest("plan it", "you are briar", tmp_path / "worktree", MAX_USD, {"type": "object"}, Access.READ)

        answer = asyncio.run(ClaudeAgent("claude-test", guard, SandboxRoom(("pypi.org",), tmp_path / "tools")).ask(request))

        options = sdk.options[0]
        expected = ("claude-test", str(tmp_path / "worktree"), "you are briar", MAX_USD, "default", [])
        assert (options.model, options.cwd, options.system_prompt, options.max_budget_usd, options.permission_mode, options.setting_sources) == expected
        assert (options.output_format, isinstance(options.can_use_tool, ToolGate)) == ({"type": "json_schema", "schema": {"type": "object"}}, True)
        assert (answer.text, answer.structured, answer.cost_usd, answer.failed) == ("the plan", {"summary": "s", "steps": []}, COST, False)

    def test_bash_runs_sandboxed_with_only_the_allowed_domains(self, guard: BashGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """V12, V13: no way out of the sandbox, and bash still goes through the allowlist."""
        sdk = SdkQuery(Results().message("success", {}))
        monkeypatch.setattr("claude_agent_sdk.query", sdk.query)

        asyncio.run(ClaudeAgent("claude-test", guard, SandboxRoom(("pypi.org",), tmp_path / "tools")).ask(AgentRequest("build", "s", tmp_path, MAX_USD)))

        network = {"allowedDomains": ["pypi.org"], "allowMachLookup": ["com.apple.SystemConfiguration.configd"]}
        assert sdk.options[0].sandbox == {"enabled": True, "autoAllowBashIfSandboxed": False, "allowUnsandboxedCommands": False, "network": network}

    def test_the_tools_of_bash_get_a_cache_folder_they_may_write(self, guard: BashGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Validation run, 2026-11-13: `uv run` in the sandbox failed with `failed to open file
        ~/.cache/uv/...: Operation not permitted`, then panicked in `system-configuration`
        (`Attempted to create a NULL object`) until the `configd` lookup was allowed."""
        sdk = SdkQuery(Results().message("success", {}))
        monkeypatch.setattr("claude_agent_sdk.query", sdk.query)

        asyncio.run(ClaudeAgent("claude-test", guard, SandboxRoom((), tmp_path / "tools")).ask(AgentRequest("build", "s", tmp_path, MAX_USD)))

        options = sdk.options[0]
        assert (options.env, options.add_dirs, (tmp_path / "tools").is_dir()) == ({"XDG_CACHE_HOME": str(tmp_path / "tools")}, [str(tmp_path / "tools")], True)

    def test_a_spent_budget_is_a_failed_answer_with_its_reason(self, guard: BashGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        message = Results().message("error_max_budget_usd", {})
        error = ResultError("Claude Code returned an error result: budget", data={"subtype": "error_max_budget_usd"})
        monkeypatch.setattr("claude_agent_sdk.query", FailingQuery(message, error).query)

        answer = asyncio.run(ClaudeAgent("claude-test", guard, SandboxRoom((), tmp_path / "tools")).ask(AgentRequest("build", "s", tmp_path, MAX_USD)))

        assert (answer.failed, answer.reason, answer.cost_usd) == (True, "error_max_budget_usd", COST)

    def test_an_api_error_keeps_the_text_of_the_sdk(self, guard: BashGuard, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The real SDK, with a refused key: subtype `success`, the cause in the message (2026-11-11)."""
        text = "Claude Code returned an error result: Invalid API key · Fix external API key"
        error = ResultError(text, data={"subtype": "success", "total_cost_usd": 0})
        monkeypatch.setattr("claude_agent_sdk.query", FailingQuery(Results().message("success", {}), error).query)

        answer = asyncio.run(ClaudeAgent("claude-test", guard, SandboxRoom((), tmp_path / "tools")).ask(AgentRequest("plan", "s", tmp_path, MAX_USD)))

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
            # Real run, 2026-11-12: `2>&1` read as `&&` refused the tests with "'1'".
            ("python -m pytest tests/test_sweep.py -q 2>&1 | tail -5", ""),
            ("pytest -q >out.txt 2>&1", ""),
            ("pytest -q &> out.txt", ""),
            ("pytest -q 2>&1 && docker ps", "not in the bash allowlist of this company: 'docker'"),
            # Real run, 2026-11-12: a subshell `(ruff ...; ruff ...)` read as the command `(ruff`.
            ("pytest -q; (ruff check a.py; ruff format --check a.py) 2>&1 | tail -5", ""),
            ("(ls; docker ps)", "not in the bash allowlist of this company: 'docker'"),
            # Same run: `NAME=value` before the command read as the command.
            ("PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src python -m pytest -q 2>&1 | tail -5", ""),
            ("X=1 docker ps", "not in the bash allowlist of this company: 'docker'"),
            ("env PYTHONPATH=src python -m pytest", "not in the bash allowlist of this company: 'env'"),
            # Real run, 2026-11-13: the lines of a heredoc read as commands ('p', 'status:', 'fresh_db').
            ("python3 - <<'EOF'\np = 'a.py'\nstatus: str = 'x'\nEOF", ""),
            ("cat >> tests/test_a.py <<'E'\ndef test_a(fresh_db):\n    assert 1\nE\nls", ""),
            ("cat <<EOF\nhi\nEOF\ndocker ps", "not in the bash allowlist of this company: 'docker'"),
            # Found with it: a new line did not start a command, so only the first line was checked.
            ("ls\ndocker ps", "not in the bash allowlist of this company: 'docker'"),
            # Same run: a program of the project, by a relative path.
            (".venv/bin/python -m pytest -q", ""),
            ("./.venv/bin/ruff check src", ""),
            ("/usr/local/bin/python -c 1", "not in the bash allowlist of this company: '/usr/local/bin/python'"),
            ("../other/bin/python -c 1", "not in the bash allowlist of this company: '../other/bin/python'"),
            # Same run: the verify commands of the runbook use shell builtins.
            ("git diff --quiet origin/main HEAD -- docker_files || (echo bad; exit 1)", ""),
            ('files=$(git diff --name-only origin/main HEAD); [ -z "$files" ] || ruff check $files', ""),
            # A forbidden token is a word, not any part of one: `gh ` is in "high ".
            ('grep -rn "high priority" src', ""),
            ("git log | grep gh ", "forbidden in briar serve: 'gh ' (push, PR and comments are done by briar)"),
        ],
    )
    def test_bash_runs_only_allowed_programs(self, guard: BashGuard, command: str, refusal: str) -> None:
        assert guard.refusal("Bash", {"command": command}) == refusal

    def test_the_company_allowlist_replaces_the_default(self, tmp_path: Path) -> None:
        guard = BashGuard(tmp_path, ["docker"], [])

        assert (guard.refusal("Bash", {"command": "docker ps"}), guard.refusal("Bash", {"command": "pytest"})) == (
            "",
            "not in the bash allowlist of this company: 'pytest'",
        )

    def test_edits_stay_in_the_worktree(self, guard: BashGuard, tmp_path: Path) -> None:
        inside = guard.refusal("Edit", {"file_path": str(tmp_path / "worktree" / "src" / "a.py")})
        outside = guard.refusal("Write", {"file_path": str(tmp_path / "secrets.env")})

        assert (inside, outside) == ("", f"edits stay inside the worktree {tmp_path / 'worktree'}: {tmp_path / 'secrets.env'} is outside")

    @pytest.mark.parametrize(
        "case",
        [
            ReadCase("Read", {"file_path": "src/a.py"}),
            ReadCase("Read", {"file_path": "{knowledge}/acme.md"}),
            ReadCase("Glob", {"pattern": "**/*.py"}),
            ReadCase("Grep", {"pattern": "def ", "path": "{knowledge}"}),
            ReadCase("TodoWrite", {"todos": "[]"}),
        ],
        ids=["worktree file", "knowledge file", "glob in the worktree", "grep in the knowledge", "todo list"],
    )
    def test_reads_inside_the_worktree_and_the_knowledge_pass(self, guard: BashGuard, tmp_path: Path, case: "ReadCase") -> None:
        assert guard.refusal(case.tool, case.filled(tmp_path)) == ""

    @pytest.mark.parametrize(
        "case",
        [
            ReadCase("Read", {"file_path": "/etc/hosts"}, "/etc/hosts"),
            ReadCase("Read", {"file_path": "../secrets.env"}, "{tmp}/secrets.env"),
            ReadCase("Glob", {"pattern": "../../**/*.env"}, "{tmp}/.."),
            ReadCase("Glob", {"pattern": "/etc/*"}, "/etc"),
            ReadCase("Grep", {"pattern": "token", "path": "~"}, "~"),
        ],
        ids=["absolute path", "climbs out", "glob climbs out", "absolute glob", "home folder"],
    )
    def test_reads_outside_are_refused(self, guard: BashGuard, tmp_path: Path, case: "ReadCase") -> None:
        """V12: a review comment is text from other people; the read tools stay in."""
        outside = Path(case.outside.replace("{tmp}", str(tmp_path))).expanduser().resolve()

        assert guard.refusal(case.tool, case.tool_input) == f"reads stay inside the worktree and the knowledge folder: {outside} is outside"

    def test_other_tools_are_refused(self, guard: BashGuard) -> None:
        assert guard.refusal("WebFetch", {}) == "WebFetch is not allowed in briar serve"


class TestToolGate:
    def test_a_read_only_step_cannot_edit(self, guard: BashGuard, tmp_path: Path) -> None:
        edit = {"file_path": str(tmp_path / "worktree" / "a.py")}

        assert (ToolGate(guard, Access.READ).refusal("Edit", edit), ToolGate(guard, Access.EDIT).refusal("Edit", edit)) == (
            "this step only reads: write the answer, do not edit files",
            "",
        )

    def test_a_refusal_is_logged(self, guard: BashGuard, caplog: pytest.LogCaptureFixture) -> None:
        """Real run, 2026-11-12: a refused test command was found only in the Agent SDK transcript."""
        with caplog.at_level("INFO", logger="briar.serve.agent"):
            asyncio.run(ToolGate(guard, Access.EDIT)("Bash", {"command": "docker ps"}, None))

        assert caplog.messages == ["serve: agent Bash refused (not in the bash allowlist of this company: 'docker'): docker ps"]

    def test_the_sdk_gets_allow_or_deny(self, guard: BashGuard) -> None:
        gate = ToolGate(guard, Access.EDIT)

        allowed = asyncio.run(gate("Bash", {"command": "pytest"}, None))
        denied = asyncio.run(gate("Bash", {"command": "curl x"}, None))

        assert (type(allowed), type(denied), denied.message) == (
            PermissionResultAllow,
            PermissionResultDeny,
            "forbidden in briar serve: 'curl' (push, PR and comments are done by briar)",
        )
