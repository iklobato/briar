"""AWS Bedrock `LLMProvider` — stub.

Implements via ``boto3`` (already a runtime dep). Bedrock supports
multiple model providers behind one API: Anthropic Claude on Bedrock,
Mistral, Meta Llama, etc. Tool-call shapes differ per underlying
model — this adapter would route on `self._model` prefix."""

from __future__ import annotations

from typing import Any, Dict, List

from briar.agent._llm import LLMProvider, LLMResponse


class BedrockLLM(LLMProvider):
    kind = "bedrock"
    # AWS Bedrock model IDs are vendor-prefixed.
    DEFAULT_MODEL = "anthropic.claude-sonnet-4-20250514-v1:0"

    def __init__(self, *, model: str = "") -> None:
        self._model = model or self.DEFAULT_MODEL

    def is_available(self) -> bool:
        # Bedrock auths through the ambient AWS credential chain (IAM,
        # SSO, env vars). A truthy check would `boto3.Session().get_credentials()`
        # but that costs a network call on SSO sessions — defer to call time.
        return True

    def complete(self, *, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], max_tokens: int) -> LLMResponse:
        raise NotImplementedError(
            "BedrockLLM.complete is not implemented yet. Use boto3: "
            "client = boto3.client('bedrock-runtime'); "
            "client.converse(modelId=self._model, system=[{text:system}], "
            "messages=messages, toolConfig={tools:[{toolSpec:{name,description,inputSchema}}]}, "
            "inferenceConfig={maxTokens:max_tokens}). The response's "
            "`output.message.content[]` carries blocks of type `text` or `toolUse` "
            "(camelCase, not snake_case). For `anthropic.claude-*` models the shape "
            "is close to native Anthropic; for `meta.llama-*` it differs slightly."
        )

    def format_tool_result(self, tool_call_id: str, output: str, is_error: bool = False) -> Dict[str, Any]:
        block: Dict[str, Any] = {
            "toolResult": {
                "toolUseId": tool_call_id,
                "content": [{"text": output}],
            }
        }
        if is_error:
            block["toolResult"]["status"] = "error"
        return {"role": "user", "content": [block]}
