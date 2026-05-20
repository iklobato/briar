"""Pydantic spec models for the declarative config file.

These are the *config* shape — what the user writes on disk. Scaffold
templates build instances of these and emit JSON for the web UI to
consume.

Convention: instead of `Optional[str] = None`, every field that may be
unset defaults to its empty value (``""`` for strings, ``{}`` for
mappings, ``[]`` for lists). Code that needs to check presence uses
truthiness (``if spec.key:`` not ``if spec.key is not None``)."""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    """Forbid unknown keys — typos in the config become errors, not silent
    drops on the server."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# Catalogue resources
# ---------------------------------------------------------------------------


class LlmProviderSpec(_StrictModel):
    key: str = ""
    name: str
    kind: str = ""
    api_base: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True


class LlmModelSpec(_StrictModel):
    key: str = ""
    name: str
    provider: str = ""
    provider_key: str = ""
    display_name: str = ""
    default_params: Dict[str, Any] = Field(default_factory=dict)
    credential_binding: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True
    price_per_1k_input_usd: float = 0.0
    price_per_1k_output_usd: float = 0.0
    pricing_strategy: str = ""
    pricing_config: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_provider_ref(self) -> "LlmModelSpec":
        if not (self.provider or self.provider_key):
            raise ValueError(f"llm_models.{self.key or self.name}: either `provider` (uuid) or `provider_key` (config key) is required")
        return self


class SourceSpec(_StrictModel):
    key: str = ""
    name: str
    kind: str
    config: Dict[str, Any] = Field(default_factory=dict)
    credentials_ref: str = ""
    credential_binding: Dict[str, Any] = Field(default_factory=dict)
    cache_policy: Dict[str, Any] = Field(default_factory=dict)
    is_enabled: bool = True


class ToolSpec(_StrictModel):
    key: str = ""
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    side_effect: Literal["read", "mutate"] = "read"
    implementation_ref: str = ""
    credentials_ref: str = ""
    credential_binding: Dict[str, Any] = Field(default_factory=dict)


class AgentSpec(_StrictModel):
    key: str = ""
    name: str
    role: str = ""
    goal: str = ""
    backstory: str = ""
    system_prompt: str = ""
    system_prompt_file: str = ""
    llm_model: str = ""
    llm_model_key: str = ""
    fallback_llm_model_key: str = ""
    tool_ids: List[str] = Field(default_factory=list)
    tool_keys: List[str] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)
    source_keys: List[str] = Field(default_factory=list)
    max_iter: int = 8
    allow_delegation: bool = False
    runtime: str = "crew"
    runtime_config: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_llm_ref(self) -> "AgentSpec":
        if not (self.llm_model or self.llm_model_key):
            raise ValueError(f"agents.{self.key or self.name}: either `llm_model` (uuid) or `llm_model_key` is required")
        return self


# ---------------------------------------------------------------------------
# Workflow graph (discriminated union by node kind)
# ---------------------------------------------------------------------------


class _NodeBase(_StrictModel):
    id: str
    next: str = ""


class AgentNodeSpec(_NodeBase):
    kind: Literal["agent"]
    agent_key: str = ""
    agent_id: str = ""
    prompt: str = ""

    @model_validator(mode="after")
    def _require_agent_ref(self) -> "AgentNodeSpec":
        if not (self.agent_key or self.agent_id):
            raise ValueError(f"workflow node {self.id!r}: agent kind requires `agent_key` or `agent_id`")
        return self


class HumanCheckpointNodeSpec(_NodeBase):
    kind: Literal["human_checkpoint"]
    prompt: str
    branches: Dict[str, str] = Field(default_factory=dict)


class BranchNodeSpec(_NodeBase):
    kind: Literal["branch"]
    branches: Dict[str, str]


class SwitchNodeSpec(_NodeBase):
    kind: Literal["switch"]
    expression: str
    cases: Dict[str, str]
    default_case: str = ""


class ParallelNodeSpec(_NodeBase):
    kind: Literal["parallel"]
    parallel_agent_keys: List[str] = Field(default_factory=list)
    parallel_agent_ids: List[str] = Field(default_factory=list)
    parallel_prompts: List[str] = Field(default_factory=list)


class SubworkflowNodeSpec(_NodeBase):
    kind: Literal["subworkflow"]
    subworkflow_lineage_id: str


WorkflowNodeSpec = Annotated[
    Union[
        AgentNodeSpec,
        HumanCheckpointNodeSpec,
        BranchNodeSpec,
        SwitchNodeSpec,
        ParallelNodeSpec,
        SubworkflowNodeSpec,
    ],
    Field(discriminator="kind"),
]


class WorkflowGraphSpec(_StrictModel):
    process: Literal["sequential", "hierarchical"] = "sequential"
    entry: str
    nodes: List[WorkflowNodeSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _entry_is_a_node(self) -> "WorkflowGraphSpec":
        node_ids = {n.id for n in self.nodes}
        if self.entry not in node_ids:
            raise ValueError(f"entry node {self.entry!r} not present in nodes")
        return self


class WorkflowSpec(_StrictModel):
    key: str = ""
    name: str
    description: str = ""
    graph: WorkflowGraphSpec
    auto_merge_rules: Dict[str, Any] = Field(default_factory=dict)


class TriggerSpec(_StrictModel):
    key: str = ""
    name: str
    kind: str
    target_workflow: str = ""
    workflow_key: str = ""
    filter_rules: Dict[str, Any] = Field(default_factory=dict)
    payload_to_context_mapping: Dict[str, Any] = Field(default_factory=dict)
    signing_secret_ref: str = ""
    schedule_cron: str = ""
    is_enabled: bool = True

    @model_validator(mode="after")
    def _require_workflow_ref(self) -> "TriggerSpec":
        if not (self.target_workflow or self.workflow_key):
            raise ValueError(f"triggers.{self.key or self.name}: either `target_workflow` (uuid) or `workflow_key` is required")
        return self


# ---------------------------------------------------------------------------
# Top-level config file
# ---------------------------------------------------------------------------


class ConfigSpec(_StrictModel):
    """Top-level config bundle."""

    version: int = 1
    llm_providers: List[LlmProviderSpec] = Field(default_factory=list)
    llm_models: List[LlmModelSpec] = Field(default_factory=list)
    sources: List[SourceSpec] = Field(default_factory=list)
    tools: List[ToolSpec] = Field(default_factory=list)
    agents: List[AgentSpec] = Field(default_factory=list)
    workflows: List[WorkflowSpec] = Field(default_factory=list)
    triggers: List[TriggerSpec] = Field(default_factory=list)
