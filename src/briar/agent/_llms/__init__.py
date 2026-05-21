"""LLM provider registry."""

from __future__ import annotations

from typing import Dict, Tuple, Type

from briar.agent._llm import LLMProvider
from briar.agent._llms.anthropic_llm import AnthropicLLM
from briar.agent._llms.bedrock import BedrockLLM
from briar.agent._llms.gemini import GeminiLLM
from briar.agent._llms.openai_llm import OpenAILLM
from briar.errors import CliError


LLMS: Dict[str, Type[LLMProvider]] = {
    cls.kind: cls
    for cls in (AnthropicLLM, OpenAILLM, GeminiLLM, BedrockLLM)
}


class LLMRegistry:
    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(LLMS.keys())

    @classmethod
    def make(cls, kind: str, *, model: str = "") -> LLMProvider:
        llm_cls = LLMS.get(kind)
        if llm_cls is None:
            known = ", ".join(sorted(LLMS.keys()))
            raise CliError(f"unknown LLM provider {kind!r}; known: {known}")
        return llm_cls(model=model)


make_llm = LLMRegistry.make


__all__ = ["LLMS", "LLMProvider", "LLMRegistry", "make_llm"]
