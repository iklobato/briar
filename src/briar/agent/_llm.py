"""`LLMProvider` — vendor-neutral facade the agent runner uses instead
of constructing an Anthropic / OpenAI / Bedrock client directly.

Strategy + Registry, same shape as `RepositoryProvider` and
`TrackerProvider`. Concrete adapters live in `_llms/`.

The trick with LLM abstraction is that each vendor's tool-call format
(Anthropic `tool_use` blocks vs OpenAI `function_call` vs Bedrock's
shape) differs in both the model's *output* and the format you echo
*results* back. So this contract has two verbs: `complete` (one turn)
and `format_tool_result` (how to wire a tool's output into the next
turn's messages). The runner iterates; the provider stays stateless."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List

from briar.error_policy import ErrorPolicyRegistry


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMToolCall:
    """One tool-use request from the model."""

    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    """One turn's response, normalised across vendors.

    `raw_assistant_message` carries the provider-specific echo-back
    payload so the runner can append it to `messages` for the next
    turn without knowing the vendor's format. This is the only field
    that isn't fully normalised — every other field is portable."""

    text: str
    tool_calls: List[LLMToolCall]
    stop_reason: str
    input_tokens: int
    output_tokens: int
    raw_assistant_message: Dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Strategy contract. Adapters translate one vendor's API onto
    these two verbs."""

    kind: ClassVar[str] = ""

    @classmethod
    def default_error_policies(cls) -> ErrorPolicyRegistry:
        """The provider's default error-response policies — consumed
        by the agent runner's RetryingExecutor. Subclasses override to
        encode their SDK's exception taxonomy (rate limits, transient
        errors, 5xx, etc.). Base returns an empty registry — the
        executor will Abort on every exception, preserving the original
        "propagate everything" behaviour for providers that haven't yet
        declared their own policies."""
        return ErrorPolicyRegistry()

    @abstractmethod
    def is_available(self) -> bool:
        """True iff credentials are present and the provider is usable."""

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        """One turn. Returns the normalised response. The provider
        handles retries / rate-limit / auth internally."""

    @abstractmethod
    def format_tool_result(self, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:
        """Provider-specific shape for echoing one tool's output back
        to the model. Anthropic: ``{"type": "tool_result", ...}``;
        OpenAI: ``{"role": "tool", "tool_call_id": ..., "content": ...}``."""
