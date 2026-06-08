"""LLM provider registry + base-protocol contract.

Covers ``briar.agent._llms.__init__`` (LLMRegistry / make_llm) and the
two thin-but-load-bearing bits of ``briar.agent._llm`` (the base
``default_error_policies`` / ``required_env_vars`` defaults that every
adapter inherits or overrides).
"""

from __future__ import annotations

import pytest

from briar.agent._llm import LLMProvider, LLMResponse, LLMToolCall
from briar.agent._llms import LLMS, LLMRegistry, make_llm
from briar.error_policy import Abort, ErrorPolicyRegistry
from briar.errors import CliError

pytestmark = pytest.mark.registry


# ── registry shape ────────────────────────────────────────────────────


def test_all_four_providers_registered() -> None:
    assert set(LLMRegistry.kinds()) == {"anthropic", "openai", "gemini", "bedrock"}


def test_registry_keys_match_class_kind_attr() -> None:
    # build_registry keys on `.kind`; assert each entry's kind matches its key.
    for key, cls in LLMS.items():
        assert cls.kind == key


def test_make_returns_requested_provider() -> None:
    assert make_llm("anthropic").kind == "anthropic"
    assert make_llm("openai").kind == "openai"
    assert make_llm("gemini").kind == "gemini"
    assert make_llm("bedrock").kind == "bedrock"


def test_make_passes_model_through() -> None:
    llm = make_llm("openai", model="gpt-4o-mini")
    assert llm._model == "gpt-4o-mini"


def test_make_unknown_kind_raises_cli_error_listing_known() -> None:
    with pytest.raises(CliError) as ctx:
        make_llm("llama")
    msg = str(ctx.value)
    assert "llama" in msg
    # Error names the known kinds so the operator can self-correct.
    for kind in ("anthropic", "openai", "gemini", "bedrock"):
        assert kind in msg


def test_make_llm_is_registry_make_alias() -> None:
    # `make_llm` is the module-level export of `LLMRegistry.make`; both are
    # bound classmethods, so compare the underlying function, not identity.
    assert make_llm.__func__ is LLMRegistry.make.__func__


# ── base-protocol defaults (briar.agent._llm) ─────────────────────────


def test_base_default_error_policies_is_empty_registry() -> None:
    # The base class returns an empty registry → the executor aborts on any
    # exception (the "propagate everything" default for providers that
    # haven't declared a taxonomy). Resolving any error hits _PROPAGATE.
    reg = LLMProvider.default_error_policies()
    assert isinstance(reg, ErrorPolicyRegistry)
    assert reg.policies == ()
    decision = reg.resolve(RuntimeError("x")).decide(RuntimeError("x"), 1)
    assert isinstance(decision, Abort)


def test_base_required_env_vars_is_empty() -> None:
    assert LLMProvider.required_env_vars() == []


def test_anthropic_overrides_error_policies_non_empty() -> None:
    # Regression-pin: the Anthropic adapter must declare a real taxonomy,
    # not silently inherit the empty base.
    from briar.agent._llms.anthropic_llm import AnthropicLLM

    reg = AnthropicLLM.default_error_policies()
    assert len(reg.policies) >= 5


# ── LLMResponse / LLMToolCall value semantics ─────────────────────────


def test_llm_response_defaults_raw_message_to_empty_dict() -> None:
    # Two instances must not share a mutable default (the classic dataclass
    # field(default_factory=dict) trap).
    a = LLMResponse(text="", tool_calls=[], stop_reason="", input_tokens=0, output_tokens=0)
    b = LLMResponse(text="", tool_calls=[], stop_reason="", input_tokens=0, output_tokens=0)
    a.raw_assistant_message["k"] = "v"
    assert b.raw_assistant_message == {}


def test_llm_tool_call_is_frozen() -> None:
    call = LLMToolCall(id="t1", name="grep", arguments={"q": "x"})
    with pytest.raises(Exception):  # FrozenInstanceError (dataclasses)
        call.id = "t2"  # type: ignore[misc]
