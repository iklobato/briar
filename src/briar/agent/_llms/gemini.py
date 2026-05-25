"""Google Gemini `LLMProvider`.

Lazy-imports ``google.generativeai``; opt-in via ``pip install
briar-cli[gemini]``. Gemini's tool-call shape lives inside
``response.candidates[0].content.parts[].function_call`` (no separate
"tool_calls" array)."""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Dict, List, Optional

from briar.agent._enums import StopReason
from briar.agent._llm import LLMProvider, LLMResponse, LLMToolCall


log = logging.getLogger(__name__)


def _import_genai() -> Optional[Any]:
    try:
        return importlib.import_module("google.generativeai")
    except ImportError:
        return None


class GeminiLLM(LLMProvider):
    kind = "gemini"
    DEFAULT_MODEL = "gemini-2.5-pro"

    def __init__(self, *, model: str = "") -> None:
        self._model_name = model or self.DEFAULT_MODEL
        self._api_key = os.environ.get("GEMINI_API_KEY", "")
        self._model = None

    def is_available(self) -> bool:
        return bool(self._api_key) and _import_genai() is not None

    def _build_model(self, system: str, tools: List[Dict[str, Any]]):
        genai = _import_genai()
        if genai is None:
            raise RuntimeError("google-generativeai not installed — run `pip install briar-cli[gemini]`")
        if not self._api_key:
            raise RuntimeError("GEMINI_API_KEY env var is required for GeminiLLM")
        genai.configure(api_key=self._api_key)
        # Gemini's `tools` argument accepts a list of dicts in OpenAPI shape;
        # genai converts internally.
        gemini_tools = self._to_gemini_tools(tools) if tools else None
        return genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=system or None,
            tools=gemini_tools,
        )

    def complete(
        self,
        *,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        model = self._build_model(system, tools)
        contents = self._to_gemini_contents(messages)
        resp = model.generate_content(
            contents=contents,
            generation_config={"max_output_tokens": max_tokens},
        )
        return self._normalise(resp)

    def format_tool_result(self, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:
        # Gemini correlates tool results by *function name*, not by id —
        # callers pass the function name as `tool_call_id`.
        content = {"function_response": {"name": tool_call_id, "response": {"content": output, "is_error": is_error}}}
        return {"role": "user", "parts": [content]}

    # ---- shape translation ------------------------------------------------

    @staticmethod
    def _to_gemini_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Gemini expects a list of `Tool` objects; each contains
        # `function_declarations` with `{name, description, parameters}`.
        return [
            {
                "function_declarations": [
                    {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    }
                    for t in tools
                ]
            }
        ]

    @staticmethod
    def _to_gemini_contents(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Anthropic uses role ``user``/``assistant``; Gemini uses
        ``user``/``model``. Plain-text content gets wrapped in parts."""
        out: List[Dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "user")
            if role == "assistant":
                role = "model"
            content = m.get("content")
            if isinstance(content, str):
                out.append({"role": role, "parts": [{"text": content}]})
            else:
                out.append({"role": role, "parts": content or []})
        return out

    @staticmethod
    def _normalise(resp: Any) -> LLMResponse:
        candidates = getattr(resp, "candidates", None) or []
        if not candidates:
            return LLMResponse(text="", tool_calls=[], stop_reason="", input_tokens=0, output_tokens=0)
        candidate = candidates[0]
        parts = getattr(candidate.content, "parts", None) or []
        text_parts: List[str] = []
        tool_calls: List[LLMToolCall] = []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                text_parts.append(text)
            fc = getattr(part, "function_call", None)
            if fc is not None:
                tool_calls.append(
                    LLMToolCall(
                        id=fc.name,  # Gemini has no native id — use name
                        name=fc.name,
                        arguments=dict(fc.args or {}),
                    )
                )
        # Gemini `finish_reason`: STOP / MAX_TOKENS / SAFETY / RECITATION / OTHER
        finish = getattr(candidate, "finish_reason", None)
        finish_name = getattr(finish, "name", str(finish or ""))
        if tool_calls:
            stop = StopReason.TOOL_USE
        elif finish_name == "STOP":
            stop = StopReason.END_TURN
        else:
            stop = finish_name.lower()
        usage = getattr(resp, "usage_metadata", None)
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=stop,
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
            raw_assistant_message={"role": "model", "parts": [getattr(p, "to_dict", lambda: {})() for p in parts]},
        )
