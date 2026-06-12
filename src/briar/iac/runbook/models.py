"""Runbook YAML schema (Pydantic) — extract-only after API removal.

A runbook declares which extractors to run per company and where to
write the resulting knowledge blob. Optional fields use empty defaults
(`""`, `[]`, sentinel empty model) instead of `Optional[X] = None`.

The two name-validation field validators (`_validate_extractor_name`,
`_validate_store_name`) check against the runtime registries rather
than a hardcoded `Literal[...]` — adding a new extractor / store
backend doesn't require a schema edit. See ARCHITECTURE.md finding
#5 + #6."""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    """All schema models forbid unknown keys — typos surface at load
    time with a locator-aware Pydantic error, never silently."""

    model_config = ConfigDict(extra="forbid")


class ExtractEntry(_Strict):
    """Per-company extractor selection with its kind-specific args."""

    name: str
    args: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_extractor_name(cls, value: str) -> str:
        """Check against the live `EXTRACTORS` registry — single source
        of truth. Lazy import to avoid a cycle (extract/__init__.py
        imports the extractors, which transitively touch the runbook
        models on test discovery)."""
        from briar.extract import EXTRACTORS

        if value not in EXTRACTORS:
            known = ", ".join(sorted(EXTRACTORS.keys()))
            raise ValueError(f"unknown extractor {value!r}; known: {known}")
        return value


class ScheduleEntry(_Strict):
    """One scheduled task — a named group of extractors fired by the
    in-process `schedule` library. See `EveryParser` for the DSL."""

    task: str
    every: str
    extract: List[ExtractEntry] = Field(default_factory=list)


class KnowledgeBinding(BaseModel):
    """Where this company's knowledge blob lands on disk. An empty
    `name` means "not configured" — callers fall back to a default."""

    model_config = ConfigDict(extra="ignore")

    store: Literal["file", "postgres"] = "file"
    name: str = ""
    root: str = ""
    # Backend-specific config (mirrors MessageBinding.config). Each store's
    # `from_binding` decides which keys it understands. Today: `postgres`
    # reads `dsn_env` (an env-var NAME holding the DSN — never the DSN
    # itself, which stays out of version control).
    config: Dict[str, str] = Field(default_factory=dict)

    @field_validator("store")
    @classmethod
    def _validate_store_name(cls, value: str) -> str:
        """Belt-and-suspenders sanity check against the live
        `KnowledgeStoreRegistry`. The Literal annotation catches typos
        at parse time; this validator catches drift if a backend is
        ever removed from the registry while still allowed by Literal."""
        from briar.storage import KnowledgeStoreRegistry

        kinds = KnowledgeStoreRegistry.names()
        if value not in kinds:
            raise ValueError(f"unknown knowledge store {value!r}; known: {', '.join(sorted(kinds))}")
        return value


class MessageBinding(_Strict):
    """One named outbound-message channel for a company. Maps a human
    handle (``ticket_comment``, ``ops_chat``) to a registered
    `MessageWriter` kind plus optional per-binding config (channel
    overrides, default status names, etc.).

    Example::

      messages:
        ticket_comment: {kind: jira-comment}
        ops_chat: {kind: slack-channel}
        escalation: {kind: telegram-chat, config: {chat_env: TELEGRAM_X_ALT_CHAT}}

    Validation: ``kind`` must be a key in the live `WRITERS` registry.
    Adding a new writer needs zero schema edits."""

    kind: str
    config: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _validate_writer_kind(cls, value: str) -> str:
        from briar.messaging import WRITERS

        if value not in WRITERS:
            known = ", ".join(sorted(WRITERS.keys()))
            raise ValueError(f"unknown message writer kind {value!r}; known: {known}")
        return value


class McpServerBinding(_Strict):
    """One MCP (Model Context Protocol) server the agent may call tools on.

    A server is named by its handle (the key under the company's ``mcp:``
    block). At agent-start time the runner connects, lists the server's
    tools, and binds each one under the name ``mcp__<handle>__<tool>`` so
    the LLM can call it like any built-in tool.

    Two transports:

      * ``stdio`` — the server is a local subprocess. Set ``command`` +
        ``args``. ``env`` maps a subprocess env-var NAME → the NAME of an
        env var in briar's own environment to copy the value from — never
        the secret literal (same env-var-name indirection as
        ``KnowledgeBinding.dsn_env`` / telegram's ``chat_env``).
      * ``http`` — the server is reachable over Streamable HTTP. Set
        ``url``. ``headers`` maps a header name → the NAME of an env var
        holding its value (e.g. a bearer token).

    ``tools`` is an optional allowlist: when non-empty, only those tool
    names are bound and everything else the server advertises is dropped —
    the same opt-in narrowing principle as the rest of the agent's tool
    surface. ``enabled: false`` skips the server without deleting its block.

    Example::

      mcp:
        github:
          transport: stdio
          command: docker
          args: ["run", "-i", "--rm", "-e", "GITHUB_TOKEN", "ghcr.io/github/github-mcp-server"]
          env: {GITHUB_TOKEN: GITHUB_TOKEN}
          tools: [search_issues, get_pull_request]
        sentry:
          transport: http
          url: https://mcp.sentry.dev/mcp
          headers: {Authorization: SENTRY_MCP_BEARER}
    """

    transport: Literal["stdio", "http"] = "stdio"
    command: str = ""
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: Dict[str, str] = Field(default_factory=dict)
    tools: List[str] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="after")
    def _check_transport_fields(self) -> "McpServerBinding":
        """A stdio server is nothing without a command; an http server is
        nothing without a url. Catch the misconfiguration at load time
        with a locator-aware error rather than at first tool call."""
        if self.transport == "stdio" and not self.command:
            raise ValueError("mcp stdio server requires a non-empty `command`")
        if self.transport == "http" and not self.url:
            raise ValueError("mcp http server requires a non-empty `url`")
        return self


class GitIdentity(BaseModel):
    """Per-company commit identity used by ``briar agent`` flows.

    Honoured by ``briar agent prfix`` and ``briar agent implement``.
    Empty model = "not configured" — falls back to the agent CLI's
    ``--git-user-name`` / ``--git-user-email`` flags or the legacy
    hardcoded defaults. Each field resolves independently: you can
    set ``name`` in YAML and override only ``email`` from the CLI."""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    email: str = ""


class CompanyEntry(BaseModel):
    """Company-level extraction config. Ignores unknown keys so legacy
    YAMLs that still carry `runbooks:`, `defaults:`, etc. still load."""

    model_config = ConfigDict(extra="ignore")

    profile: str = ""
    workspace_id: str = ""
    knowledge_file: str = ""
    knowledge: KnowledgeBinding = Field(default_factory=KnowledgeBinding)
    extract: List[ExtractEntry] = Field(default_factory=list)
    schedules: List[ScheduleEntry] = Field(default_factory=list)
    # Optional named outbound-message channels. The agent's
    # `SendMessageTool` resolves handle → writer at run time. Empty
    # dict means the agent cannot send messages (it falls back to
    # the bash escape hatch for `gh` / `curl`).
    messages: Dict[str, MessageBinding] = Field(default_factory=dict)
    # Optional named MCP (Model Context Protocol) servers. The agent
    # connects to each at run time, lists its tools, and binds them as
    # `mcp__<handle>__<tool>`. Empty dict means no MCP tools are bound.
    mcp: Dict[str, McpServerBinding] = Field(default_factory=dict)
    # Per-company commit author for `briar agent` worktree commits.
    # Read by AgentCommand._resolve_git_identity at run time when
    # `--runbook` points at a YAML containing this block.
    git_identity: GitIdentity = Field(default_factory=GitIdentity)


class RunbookFile(_Strict):
    version: int = 1
    companies: Dict[str, CompanyEntry] = Field(min_length=1)
