"""Anthropic `LLMProvider`. Captures the call shape that
`agent/runner.py` previously inlined: OAuth `auth_token` + the
`oauth-2025-04-20` beta header, the 5-attempt rate-limit retry
schedule, and the `tool_use` block normalisation."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List

from briar.agent._llm import LLMProvider, LLMResponse, LLMToolCall


log = logging.getLogger(__name__)


class AnthropicLLM(LLMProvider):
    kind = "anthropic"
    DEFAULT_MODEL = "claude-sonnet-4-5"
    # Backoff: 30s, 60s, 120s, 240s, 480s — 5 attempts total.
    RETRY_WAITS = (0, 30, 60, 120, 240)

    def __init__(self, *, model: str = "") -> None:
        self._model = model or self.DEFAULT_MODEL
        self._oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = None

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

    def complete(
        self,
        *,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        import anthropic

        client = self._build_client()
        last_exc: Exception = RuntimeError("unreachable")
        for attempt, wait in enumerate(self.RETRY_WAITS, start=1):
            if wait > 0:
                log.info("anthropic-rate-limit: sleeping %ds before attempt %d", wait, attempt)
                time.sleep(wait)
            try:
                response = client.messages.create(
                    model=self._model,
                    max_tokens=max_tokens,
                    system=system,
                    tools=tools,
                    messages=messages,
                )
                return self._normalise(response)
            except anthropic.RateLimitError as exc:
                log.warning("anthropic-429: attempt=%d/%d err=%s", attempt, len(self.RETRY_WAITS), exc)
                last_exc = exc
                continue
        log.error("anthropic-429-exhausted: %d retries", len(self.RETRY_WAITS))
        raise last_exc

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
