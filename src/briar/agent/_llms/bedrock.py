"""AWS Bedrock `LLMProvider`.

Uses the unified Bedrock ``Converse`` API which abstracts over the
provider-specific protocols (Anthropic, Mistral, Meta, Cohere all
expose the same `converse(modelId, system, messages, toolConfig)`
surface).

Auth: ambient AWS credential chain (boto3 default). Region comes from
``AWS_REGION`` or the boto3 default chain.

Model IDs are vendor-prefixed (e.g.
``anthropic.claude-sonnet-4-20250514-v1:0``,
``meta.llama3-70b-instruct-v1:0``). The Converse API normalises the
response shape across all of them — this adapter doesn't branch on
the prefix."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from briar.agent._llm import LLMProvider, LLMResponse, LLMToolCall


log = logging.getLogger(__name__)


class BedrockLLM(LLMProvider):
    kind = "bedrock"
    DEFAULT_MODEL = "anthropic.claude-sonnet-4-20250514-v1:0"

    def __init__(self, *, model: str = "") -> None:
        self._model = model or self.DEFAULT_MODEL
        self._client = None

    def _make_client(self):
        if self._client is not None:
            return self._client
        import boto3

        self._client = boto3.client("bedrock-runtime")
        return self._client

    def is_available(self) -> bool:
        try:
            import boto3  # noqa: F401

            return True
        except ImportError:
            return False

    def complete(
        self,
        *,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        client = self._make_client()
        # Translate Anthropic-shaped `tools` (name/description/input_schema)
        # onto Bedrock's toolConfig.tools[].toolSpec format.
        tool_config = self._to_bedrock_tools(tools)
        kwargs: Dict[str, Any] = {
            "modelId": self._model,
            "system": [{"text": system}] if system else [],
            "messages": self._to_bedrock_messages(messages),
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if tool_config:
            kwargs["toolConfig"] = tool_config

        resp = client.converse(**kwargs)
        return self._normalise(resp)

    def format_tool_result(self, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "toolResult": {
                "toolUseId": tool_call_id,
                "content": [{"text": output}],
            }
        }
        if is_error:
            result["toolResult"]["status"] = "error"
        return {"role": "user", "content": [result]}

    # ---- shape translation ------------------------------------------------

    @staticmethod
    def _to_bedrock_tools(tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        out: List[Dict[str, Any]] = []
        for t in tools:
            out.append(
                {
                    "toolSpec": {
                        "name": t.get("name", ""),
                        "description": t.get("description", ""),
                        "inputSchema": {"json": t.get("input_schema", {})},
                    }
                }
            )
        return {"tools": out} if out else {}

    @staticmethod
    def _to_bedrock_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Bedrock content blocks differ from Anthropic's: Anthropic uses
        ``{type: tool_result, tool_use_id, content}``; Bedrock uses
        ``{toolResult: {toolUseId, content}}``. The format_tool_result
        method emits the Bedrock shape already; this just passes things
        through, only translating the simple ``{role, content: <str>}``
        case where the user message is plain text."""
        out: List[Dict[str, Any]] = []
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                out.append({"role": m.get("role", "user"), "content": [{"text": content}]})
            else:
                out.append(m)
        return out

    @staticmethod
    def _normalise(resp: Dict[str, Any]) -> LLMResponse:
        output = (resp.get("output") or {}).get("message") or {}
        blocks = output.get("content") or []
        text_parts: List[str] = []
        tool_calls: List[LLMToolCall] = []
        for block in blocks:
            if "text" in block:
                text_parts.append(str(block["text"]))
            elif "toolUse" in block:
                tu = block["toolUse"]
                tool_calls.append(
                    LLMToolCall(
                        id=str(tu.get("toolUseId") or ""),
                        name=str(tu.get("name") or ""),
                        arguments=dict(tu.get("input") or {}),
                    )
                )
        usage = resp.get("usage") or {}
        # Bedrock reports `end_turn` / `tool_use` / `max_tokens` / `stop_sequence`
        # in `stopReason` — same vocabulary as Anthropic, snake-cased.
        stop = str(resp.get("stopReason") or "")
        if stop == "endTurn":
            stop = "end_turn"
        elif stop == "toolUse":
            stop = "tool_use"
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=stop,
            input_tokens=int(usage.get("inputTokens") or 0),
            output_tokens=int(usage.get("outputTokens") or 0),
            raw_assistant_message={"role": "assistant", "content": blocks},
        )
