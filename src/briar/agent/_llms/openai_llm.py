"""OpenAI `LLMProvider` — stub.

Implements is_available() against ``OPENAI_API_KEY``; ``complete`` and
``format_tool_result`` raise ``NotImplementedError`` with the exact
shape mismatch the next reader needs to handle: OpenAI uses
``function_call`` blocks (not ``tool_use``) and echoes results via
``{"role": "tool", "tool_call_id": ..., "content": ...}`` (not the
Anthropic ``tool_result`` shape). The runner is shape-agnostic
because LLMProvider.format_tool_result hides this — but the adapter
itself has to know both directions."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from briar.agent._llm import LLMProvider, LLMResponse


class OpenAILLM(LLMProvider):
    kind = "openai"
    DEFAULT_MODEL = "gpt-4o"

    def __init__(self, *, model: str = "") -> None:
        self._model = model or self.DEFAULT_MODEL
        self._api_key = os.environ.get("OPENAI_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(self, *, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], max_tokens: int) -> LLMResponse:
        raise NotImplementedError(
            "OpenAILLM.complete is not implemented yet. Use the `openai` Python SDK: "
            "client.chat.completions.create(model=self._model, "
            "messages=[{role:'system', content:system}, *messages], "
            "tools=[{type:'function', function:{name, description, parameters}} for t in tools], "
            "max_tokens=max_tokens). The response's `choices[0].message.tool_calls[]` "
            "have shape `{id, type:'function', function:{name, arguments(JSON-string)}}` — "
            "parse `function.arguments` (it's a JSON string, NOT a dict) into LLMToolCall.arguments."
        )

    def format_tool_result(self, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": output}
