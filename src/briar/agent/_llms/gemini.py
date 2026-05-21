"""Google Gemini `LLMProvider` — stub.

Implement via ``google-generativeai`` SDK. Gemini's tool-call shape
uses ``functionCall`` parts inside ``content.parts[]``."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from briar.agent._llm import LLMProvider, LLMResponse


class GeminiLLM(LLMProvider):
    kind = "gemini"
    DEFAULT_MODEL = "gemini-2.5-pro"

    def __init__(self, *, model: str = "") -> None:
        self._model = model or self.DEFAULT_MODEL
        self._api_key = os.environ.get("GEMINI_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(self, *, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], max_tokens: int) -> LLMResponse:
        raise NotImplementedError(
            "GeminiLLM.complete is not implemented yet. Use ``google-generativeai``: "
            "genai.configure(api_key=self._api_key); model = genai.GenerativeModel("
            "model_name=self._model, tools=tools, system_instruction=system). "
            "Convert Anthropic-shaped messages into Gemini's `contents=[{role, parts}]` "
            "(role 'user'/'model', not 'user'/'assistant'). Tool calls land in "
            "`response.candidates[0].content.parts[].function_call` as `{name, args}`."
        )

    def format_tool_result(self, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:
        # Gemini echoes results as `function_response` parts. tool_call_id isn't
        # used (Gemini correlates by name + order).
        return {"role": "user", "parts": [{"function_response": {"name": tool_call_id, "response": {"content": output}}}]}
