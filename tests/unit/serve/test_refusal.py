"""WP1 of the production plan: what the SDK says when the login of the model is refused or the plan limit is hit.
The messages are the real dataclasses of the SDK, in the order the real CLI sent them in the probes of 2026-11-19
(a refused API key and a refused OAuth token, both with fake credentials): an assistant message with
`error="authentication_failed"`, a result with `api_error_status=401`, then the SDK raises `ResultError`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, List, Optional

import pytest
from claude_agent_sdk import AssistantMessage, RateLimitEvent, RateLimitInfo, ResultError, ResultMessage

from briar.serve.agent import AgentRequest, AgentAnswer, BashGuard, ClaudeAgent, Refusal, SandboxRoom

MAX_USD = 2.5
UNAUTHORIZED = 401
TOO_MANY = 429
SERVER_ERROR = 500
RESETS_AT = 1_790_000_000
INVALID_KEY = "Invalid API key · Fix external API key"
INVALID_TOKEN = "Failed to authenticate. API Error: 401 OAuth access token is invalid."
LIMIT_TEXT = "You have hit your limit"


@dataclass(frozen=True)
class Stream:
    """Stands in for `claude_agent_sdk.query`: yields the messages, then raises `error` when there is one."""

    messages: List[Any]
    error: Optional[Exception] = None

    async def query(self, prompt: str, options: Any) -> AsyncIterator[Any]:
        for message in self.messages:
            yield message
        if self.error is not None:
            raise self.error


class Messages:
    def assistant(self, error: Optional[str]) -> AssistantMessage:
        return AssistantMessage(content=[], model="claude-test", error=error)  # type: ignore[arg-type]

    def failure(self, text: str, status: Optional[int] = None) -> ResultMessage:
        """The result of a call the CLI could not finish: `is_error`, and the HTTP status of the API call when it has one."""
        return ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1, session_id="s",
            result=text, total_cost_usd=0.0, api_error_status=status,
        )  # fmt: skip

    def success(self, text: str) -> ResultMessage:
        return ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="s", result=text, total_cost_usd=0.0)

    def budget(self) -> ResultMessage:
        return ResultMessage(
            subtype="error_max_budget_usd", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1, session_id="s", result="stopped", total_cost_usd=0.0
        )

    def limit(self, status: str) -> RateLimitEvent:
        window = RateLimitInfo(status=status, rate_limit_type="five_hour", resets_at=RESETS_AT)  # type: ignore[arg-type]
        return RateLimitEvent(rate_limit_info=window, uuid="u", session_id="s")

    def raised(self, text: str) -> ResultError:
        return ResultError(f"Claude Code returned an error result: {text} (exit code: 1)", data={"subtype": "success", "total_cost_usd": 0})


@dataclass(frozen=True)
class Call:
    """One call: what the SDK yielded, whether it then raised, and the refusal briar must read."""

    messages: List[Any]
    raised: Optional[Exception]
    refusal: Refusal
    failed: bool = True


class Calls:
    """The calls of the probes (real) and of the limit (built from the dataclasses the SDK documents)."""

    def refused_key(self) -> Call:
        found = [Messages().assistant("authentication_failed"), Messages().failure(INVALID_KEY, UNAUTHORIZED)]
        return Call(found, Messages().raised(INVALID_KEY), Refusal.LOGIN)

    def refused_token(self) -> Call:
        found = [Messages().assistant("authentication_failed"), Messages().failure(INVALID_TOKEN, UNAUTHORIZED)]
        return Call(found, Messages().raised(INVALID_TOKEN), Refusal.LOGIN)

    def assistant_only(self) -> Call:
        """`authentication_failed` with no HTTP status on the result: still a refused login."""
        return Call([Messages().assistant("authentication_failed"), Messages().failure("boom")], None, Refusal.LOGIN)

    def unauthorized_only(self) -> Call:
        return Call([Messages().failure(INVALID_KEY, UNAUTHORIZED)], None, Refusal.LOGIN)

    def limit_rejected(self) -> Call:
        found = [Messages().limit("rejected"), Messages().failure(LIMIT_TEXT, TOO_MANY)]
        return Call(found, Messages().raised(LIMIT_TEXT), Refusal.LIMIT)

    def limit_error_only(self) -> Call:
        return Call([Messages().assistant("rate_limit"), Messages().failure(LIMIT_TEXT)], None, Refusal.LIMIT)

    def billing_error(self) -> Call:
        return Call([Messages().assistant("billing_error"), Messages().failure("credit balance is too low")], None, Refusal.LIMIT)

    def warning_then_success(self) -> Call:
        return Call([Messages().limit("allowed_warning"), Messages().success("the plan")], None, Refusal.NONE, failed=False)

    def rejected_but_answered(self) -> Call:
        """An event about a window that is not the one that decides: the call still gave its answer, so it is no refusal."""
        return Call([Messages().limit("rejected"), Messages().success("the plan")], None, Refusal.NONE, failed=False)

    def budget_stop(self) -> Call:
        return Call([Messages().budget()], ResultError("budget", data={"subtype": "error_max_budget_usd"}), Refusal.NONE)

    def ordinary_failure(self) -> Call:
        return Call([Messages().assistant("server_error"), Messages().failure("boom", SERVER_ERROR)], Messages().raised("boom"), Refusal.NONE)


class TestRefusalOfTheModel:
    @pytest.mark.parametrize(
        "call",
        [
            Calls().refused_key(),
            Calls().refused_token(),
            Calls().assistant_only(),
            Calls().unauthorized_only(),
            Calls().limit_rejected(),
            Calls().limit_error_only(),
            Calls().billing_error(),
            Calls().warning_then_success(),
            Calls().rejected_but_answered(),
            Calls().budget_stop(),
            Calls().ordinary_failure(),
        ],
        ids=[
            "key",
            "oauth-token",
            "assistant-only",
            "401-only",
            "limit-rejected",
            "rate-limit-error",
            "billing",
            "warning",
            "rejected-but-answered",
            "budget",
            "server-error",
        ],
    )
    def test_the_answer_names_what_the_sdk_said(self, call: Call, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("claude_agent_sdk.query", Stream(call.messages, call.raised).query)
        agent = ClaudeAgent("claude-test", BashGuard(tmp_path, [], []), SandboxRoom((), tmp_path / "tools"))

        answer: AgentAnswer = asyncio.run(agent.ask(AgentRequest("plan", "s", tmp_path, MAX_USD)))

        assert (answer.refusal, answer.failed) == (call.refusal, call.failed)

    def test_a_refused_login_keeps_the_text_of_the_cli_as_its_reason(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        call = Calls().refused_token()
        monkeypatch.setattr("claude_agent_sdk.query", Stream(call.messages, call.raised).query)
        agent = ClaudeAgent("claude-test", BashGuard(tmp_path, [], []), SandboxRoom((), tmp_path / "tools"))

        answer = asyncio.run(agent.ask(AgentRequest("plan", "s", tmp_path, MAX_USD)))

        assert INVALID_TOKEN in answer.reason
