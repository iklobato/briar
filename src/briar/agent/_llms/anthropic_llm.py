"""Anthropic `LLMProvider`. Captures the call shape that
`agent/runner.py` previously inlined: OAuth `auth_token` + the
`oauth-2025-04-20` beta header, the rate-limit retry schedule
(now expressed declaratively via the error-policy registry), and
the `tool_use` block normalisation."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from briar.agent._llm import LLMProvider, LLMResponse, LLMToolCall
from briar.error_policy import (
    Abort,
    ErrorPolicyRegistry,
    ExceptionTypePolicy,
    HttpStatusPolicy,
    RetryAfter,
    RetryingExecutor,
)


log = logging.getLogger(__name__)


class AnthropicLLM(LLMProvider):
    kind = "anthropic"
    DEFAULT_MODEL = "claude-sonnet-4-5"

    @classmethod
    def default_error_policies(cls) -> ErrorPolicyRegistry:
        """Anthropic's SDK exception taxonomy → retry/abort strategy.

        Order is intentional — more-specific matches come first. The
        ``HttpStatusPolicy(APIStatusError, status=…)`` entries fire
        before the broad ``ExceptionTypePolicy(APIStatusError, …)``
        fallback, so specific status codes get tailored waits.

        Tunable: edit the ``wait_seconds`` values below or compose an
        overlay via ``registry.with_(extra_policy)`` from a company's
        YAML if/when that lands."""
        import anthropic

        return ErrorPolicyRegistry(
            policies=(
                # 429 — rate limit. Abort fast rather than sleep silently
                # for an hour: the previous `RetryAfter(3600)` × 5 attempts
                # could wedge the agent for 5 hours of `hrtimer_nanosleep`
                # with `load=0.00`, indistinguishable from a hang. That's
                # what made an OAuth subscription's burst-throttle look
                # like the CLI was broken.
                #
                # Failure modes the new policy makes obvious instead of
                # hidden:
                #  * Developer-API quota exhausted — operator decides
                #    whether to wait for the hourly reset or rotate keys.
                #  * OAuth subscription window exhausted — operator must
                #    wait for the 5-hour Claude.ai window to roll over;
                #    automated retry won't help, no `retry-after` header
                #    is provided by Anthropic for OAuth 429s.
                #  * Concurrency limit hit by running two briar processes
                #    against one token — operator serialises them.
                #
                # Anthropic's `x-should-retry: true` header doesn't change
                # this: it tells you the request CAN be retried, not that
                # retrying NOW will succeed. Surface the 429 to the caller;
                # let them decide. Re-run is cheap.
                ExceptionTypePolicy(
                    exception_type=anthropic.RateLimitError,
                    decision=Abort(reason="anthropic 429 rate-limited — wait for the quota window to reset (API key: ~1h; OAuth: up to 5h) then re-run. No retry-after header is provided."),
                ),
                # Transient TCP-level / DNS / TLS errors.
                ExceptionTypePolicy(
                    exception_type=anthropic.APIConnectionError,
                    decision=RetryAfter(wait_seconds=10, reason="anthropic transient connect"),
                ),
                # Targeted 503 (service-unavailable) — short retry.
                HttpStatusPolicy(
                    exception_type=anthropic.APIStatusError,
                    status=503,
                    decision=RetryAfter(wait_seconds=30, reason="anthropic 503 service unavailable"),
                ),
                # 529 — Anthropic's "overloaded" signal. Longer wait.
                HttpStatusPolicy(
                    exception_type=anthropic.APIStatusError,
                    status=529,
                    decision=RetryAfter(wait_seconds=120, reason="anthropic 529 overloaded"),
                ),
                # 401 / 403 — auth misconfig. No amount of retrying
                # will fix it; abort fast so the operator sees it.
                HttpStatusPolicy(
                    exception_type=anthropic.APIStatusError,
                    status=401,
                    decision=Abort(reason="anthropic 401 — check CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY"),
                ),
                HttpStatusPolicy(
                    exception_type=anthropic.APIStatusError,
                    status=403,
                    decision=Abort(reason="anthropic 403 — forbidden (model access / billing)"),
                ),
            ),
        )

    def __init__(self, *, model: str = "") -> None:
        self._model = model or self.DEFAULT_MODEL
        self._oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = None
        # Build the executor once per provider instance. The registry
        # is immutable; reusing the same executor across .complete()
        # calls preserves the agent's retry budget across one task.
        self._executor = RetryingExecutor(
            registry=self.default_error_policies(),
            max_attempts=5,
        )

    def _build_client(self):
        if self._client is not None:
            return self._client
        import anthropic

        if self._oauth_token:
            # Subscription billing via OAuth; the beta header is required.
            log.info("anthropic-auth: using CLAUDE_CODE_OAUTH_TOKEN")
            self._client = anthropic.Anthropic(
                auth_token=self._oauth_token,
                default_headers={"anthropic-beta": "oauth-2025-04-20"},
            )
        elif self._api_key:
            log.info("anthropic-auth: using ANTHROPIC_API_KEY")
            self._client = anthropic.Anthropic(api_key=self._api_key)
        else:
            raise RuntimeError("Anthropic: CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY required")
        return self._client

    def is_available(self) -> bool:
        return bool(self._oauth_token or self._api_key)

    @classmethod
    def required_env_vars(cls) -> List[str]:
        # Either-or, not both — surfaced as one comma-joined entry so
        # the runner's error message reads naturally:
        # "set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY"
        return ["CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"]

    def complete(
        self,
        *,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        """One turn. The executor handles every retryable failure mode
        declared in ``default_error_policies()`` — this method has zero
        ``try / except`` of its own. Adding a new error class is a
        registry entry, not a code change here."""
        client = self._build_client()

        def _call() -> LLMResponse:
            response = client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            )
            return self._normalise(response)

        return self._executor.run(_call)

    def format_tool_result(self, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_call_id,
            "content": output,
        }
        if is_error:
            result["is_error"] = True
        return result

    @staticmethod
    def _normalise(response) -> LLMResponse:
        """Translate Anthropic's typed response into LLMResponse."""
        tool_calls: List[LLMToolCall] = []
        text_parts: List[str] = []
        for block in response.content:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                text_parts.append(getattr(block, "text", ""))
            elif block_type == "tool_use":
                tool_calls.append(
                    LLMToolCall(
                        id=getattr(block, "id", ""),
                        name=getattr(block, "name", ""),
                        arguments=dict(getattr(block, "input", {}) or {}),
                    )
                )
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=str(getattr(response, "stop_reason", "") or ""),
            input_tokens=int((getattr(response, "usage", None) and response.usage.input_tokens) or 0),
            output_tokens=int((getattr(response, "usage", None) and response.usage.output_tokens) or 0),
            raw_assistant_message={
                "role": "assistant",
                "content": [b.model_dump() for b in response.content],
            },
        )
