"""OpenAI `LLMProvider`.

Lazy-imports ``openai`` so the package is an opt-in extra
(``pip install briar-cli[openai]``). ``is_available()`` reports False
when the SDK isn't installed; ``complete()`` raises a clear message
pointing at the right install command."""

from __future__ import annotations

import importlib
import json
import logging
import os
from typing import Any, Dict, List, Optional

from briar.agent._llm import LLMProvider, LLMResponse, LLMToolCall


log = logging.getLogger(__name__)


def _import_openai() -> Optional[Any]:
    try:
        return importlib.import_module("openai")
    except ImportError:
        return None


class OpenAILLM(LLMProvider):
    kind = "openai"
    DEFAULT_MODEL = "gpt-4o"

    def __init__(self, *, model: str = "") -> None:
        self._model = model or self.DEFAULT_MODEL
        self._api_key = os.environ.get("OPENAI_API_KEY", "")
        self._client = None

    def is_available(self) -> bool:
        return bool(self._api_key) and _import_openai() is not None

    def _build_client(self):
        if self._client is not None:
            return self._client
        openai = _import_openai()
        if openai is None:
            raise RuntimeError("openai package not installed — run `pip install briar-cli[openai]`")
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY env var is required for OpenAILLM")
        self._client = openai.OpenAI(api_key=self._api_key)
        return self._client

    def complete(
        self,
        *,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        client = self._build_client()
        api_messages: List[Dict[str, Any]] = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = self._to_openai_tools(tools)

        resp = client.chat.completions.create(**kwargs)
        return self._normalise(resp)

    def format_tool_result(self, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:
        # OpenAI's echo-back shape: a top-level "tool" message with the call id.
        # `is_error` is signalled via content prefix since the API has no native flag.
        content = f"[ERROR] {output}" if is_error else output
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}

    @staticmethod
    def _to_openai_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for t in tools:
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    },
                }
            )
        return out

    @staticmethod
    def _normalise(resp: Any) -> LLMResponse:
        choice = resp.choices[0]
        message = choice.message
        text = message.content or ""
        tool_calls: List[LLMToolCall] = []
        for tc in (message.tool_calls or []) if hasattr(message, "tool_calls") else []:
            # OpenAI delivers `arguments` as a JSON string — parse it for the caller.
            raw_args = getattr(tc.function, "arguments", "") or "{}"
            try:
                args = json.loads(raw_args)
            except (ValueError, TypeError):
                args = {}
            tool_calls.append(LLMToolCall(id=tc.id, name=tc.function.name, arguments=args))
        # Map OpenAI's `finish_reason` onto the abstraction's vocabulary.
        finish_reason = choice.finish_reason or ""
        if finish_reason == "stop":
            stop = "end_turn"
        elif finish_reason == "tool_calls":
            stop = "tool_use"
        else:
            stop = finish_reason
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            raw_assistant_message=message.model_dump() if hasattr(message, "model_dump") else {"role": "assistant", "content": text},
        )
