"""Agent runner — LLM-provider-driven tool-use loop.

Loads the archetype's system prompt, splices in the company's
knowledge, and drives an `LLMProvider.complete` loop until the model
returns `end_turn` (or we hit guardrails). Tool calls dispatch to the
`BashTool` / `ReadFileTool` / `WriteFileTool` / `EditFileTool` primitives
in `briar.agent.tools`.

Provider-agnostic: selecting Anthropic / OpenAI / Gemini / Bedrock is a
constructor arg. The runner reads normalised `LLMResponse` shapes so
this file doesn't grow per-vendor branches.
"""

from __future__ import annotations

import logging
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from briar.agent._enums import StopReason
from briar.agent._llm import LLMProvider, LLMToolCall
from briar.agent._llms import make_llm
from briar.agent.tools import BashTool, EditFileTool, ReadFileTool, SendMessageTool, ToolError, WriteFileTool
from briar.iac.scaffold.archetypes import ARCHETYPES
from briar.log_context import log_context

log = logging.getLogger(__name__)


@dataclass
class AgentRunResult:
    """Outcome of one agent run — what happened, what to report."""

    company: str
    task: str
    iterations: int = 0
    stop_reason: str = ""
    final_text: str = ""
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    commits: List[str] = field(default_factory=list)
    error: str = ""

    def cost_summary(self) -> str:
        # Sonnet 4.5 list pricing as of 2026-05: $3/M input, $15/M output.
        cost = (self.input_tokens / 1_000_000) * 3.0 + (self.output_tokens / 1_000_000) * 15.0
        return f"in={self.input_tokens:,} out={self.output_tokens:,} ≈${cost:.3f}"


@dataclass(frozen=True)
class AgentRunConfig:
    """Everything AgentRunner needs except its LLM.

    Per ARCHITECTURE_MAP.md §17 step 2 + §21: replaces 13 keyword-only
    constructor parameters with a single value object. Frozen so once
    constructed the config is immutable; tests build one and reuse it.

    `oauth_token` is gone — the Anthropic adapter reads its OAuth/API
    token directly from env. `llm_kind` stays a separate AgentRunner
    constructor arg because it controls *which* LLM provider to build
    rather than configuring the run itself.
    """

    company: str
    task: str
    archetype_name: str
    workdir: Path
    knowledge_store: Any
    target: str
    model: str = ""
    max_iterations: int = 30
    extra_user_instructions: str = ""
    task_context_sections: Tuple[Any, ...] = ()
    dry_run: bool = False
    messages: Mapping[str, Any] = field(default_factory=dict)
    mcp_servers: Mapping[str, Any] = field(default_factory=dict)
    # Handles of always-on servers (the built-in defaults): connected
    # unconditionally and excluded from the router, so two trivial local
    # tools never trigger a pointless routing call. See briar.mcp.defaults.
    mcp_always_on: Tuple[str, ...] = ()


class AgentRunner:
    """One-shot agent execution against a single (company, task) target.

    Loads the archetype's persona + the spliced knowledge for the company,
    builds the system prompt, then drives the Anthropic API tool-use loop
    until the model is done or we hit a guardrail. All tool side effects
    are confined to the worktree the caller hands us.
    """

    DEFAULT_MAX_ITERATIONS = 30
    DEFAULT_MAX_TOKENS_PER_TURN = 8_000

    def __init__(
        self,
        config: AgentRunConfig,
        *,
        llm: Optional[LLMProvider] = None,
        llm_kind: str = "anthropic",
    ) -> None:
        self._cfg = config
        self._workdir = config.workdir.resolve()
        self._archetype = ARCHETYPES[config.archetype_name]
        self._llm: LLMProvider = llm or make_llm(llm_kind, model=config.model)
        self._max_iterations = config.max_iterations or self.DEFAULT_MAX_ITERATIONS
        # Tools share the same root list so the agent can read/write
        # inside the worktree but nowhere else.
        roots = [self._workdir]
        self._bash = BashTool(base_cwd=self._workdir)
        self._read = ReadFileTool(allowed_roots=roots)
        self._write = WriteFileTool(allowed_roots=roots)
        self._edit = EditFileTool(allowed_roots=roots)
        # Only bind the SendMessageTool when the runbook actually has
        # message channels configured for this company. Empty messages
        # dict → tool not registered → LLM falls back to bash.
        self._send = SendMessageTool(messages=dict(config.messages), company=config.company) if config.messages else None
        # Dispatch registry — same instances as the attrs above, keyed by
        # tool name so `_dispatch_tool` is a lookup, not an if-ladder.
        # `_send` is registered only when channels are configured.
        self._tools: Dict[str, Any] = {tool.name: tool for tool in (self._bash, self._read, self._write, self._edit)}
        if self._send is not None:
            self._tools[self._send.name] = self._send
        # MCP servers (opt-in, same as SendMessageTool). Only the
        # archetype-scoping (Lever 3) — pure config, no I/O — happens here.
        # Connection + the router pre-pass (Lever 4) are deferred to
        # `_setup_mcp`, called from `run()` AFTER the dry-run / credentials
        # gates so a constructor never spawns subprocesses or spends tokens.
        self._mcp: Optional[Any] = None  # McpClientManager, built lazily in _setup_mcp
        self._mcp_tools: List[Any] = []
        self._router_usage: Tuple[int, int] = (0, 0)
        self._scoped_servers = self._scope_mcp_servers(config.mcp_servers)

    def _scope_mcp_servers(self, servers: Mapping[str, Any]) -> Dict[str, Any]:
        """Keep only the servers this archetype is allowed to use (Lever 3).

        A server's `archetypes` allowlist is empty → available to every
        archetype; otherwise it binds only when this run's archetype is in
        the list. Scoping the menu per task is the cheapest way to sharpen
        the model's tool-selection judgment: fewer, more-relevant choices."""
        name = self._archetype.name
        out: Dict[str, Any] = {}
        for handle, binding in (servers or {}).items():
            allow = list(getattr(binding, "archetypes", None) or [])
            if not allow or name in allow:
                out[handle] = binding
            else:
                log.debug("mcp: server=%s scoped out for archetype=%s (allowed: %s)", handle, name, allow)
        return out

    def _setup_mcp(self, *, route: bool) -> None:
        """Connect MCP servers and register their tools. Idempotent-guarded
        by the caller (run() calls it once). `route=True` runs the Lever-4
        router pre-pass first; `route=False` connects every scoped server
        (used by dry-run, which must not spend tokens)."""
        if not self._scoped_servers:
            return
        servers = self._route_servers(self._scoped_servers) if route else dict(self._scoped_servers)
        if not servers:
            return
        from briar.mcp import McpClientManager

        self._mcp = McpClientManager(servers)
        self._mcp_tools = self._mcp.start()
        for tool in self._mcp_tools:
            self._tools[tool.name] = tool

    def _route_servers(self, scoped: Dict[str, Any]) -> Dict[str, Any]:
        """Ask the LLM which scoped servers are worth connecting for this
        task (Lever 4). Always-on servers (the built-in defaults) bypass the
        router entirely — they're cheap, safe, and universally useful, so
        routing them would only waste a call. Routing runs over the rest and
        skips the LLM when there's nothing to choose between (≤1 routable).
        Fails OPEN — any routing failure connects all routable servers, so a
        flaky router never strands the agent without a capability it needs."""
        always_on = {h: b for h, b in scoped.items() if h in self._cfg.mcp_always_on}
        routable = {h: b for h, b in scoped.items() if h not in self._cfg.mcp_always_on}
        if len(routable) <= 1:
            return {**always_on, **routable}
        catalog = {handle: (getattr(binding, "purpose", "") or "") for handle, binding in routable.items()}
        selected = self._llm_select_servers(catalog)
        chosen = {handle: binding for handle, binding in routable.items() if handle in selected} if selected else {}
        return {**always_on, **(chosen or routable)}

    def _llm_select_servers(self, catalog: Dict[str, str]) -> Optional[set]:
        """One short completion: given the task + the source catalog, return
        the set of handles to enable (or None on any failure → caller fails
        open). Records token use in `self._router_usage`."""
        sources = "\n".join(f"- {handle}: {purpose or '(no description)'}" for handle, purpose in catalog.items())
        system = (
            "You route an autonomous agent to its context sources. Given the task and a list of "
            "available sources, choose which ones are worth enabling. Return ONLY a JSON array of "
            'source ids, e.g. ["github"]. Include a source if it could plausibly help the task; omit '
            "only clearly irrelevant ones. When unsure, include it."
        )
        user = f"Task:\n{self._router_task_summary()}\n\nAvailable sources:\n{sources}\n\nReturn the JSON array of source ids to enable."
        try:
            response = self._llm.complete(
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[],
                max_tokens=200,
            )
        except Exception:  # noqa: BLE001 — router must never block the run; fail open
            log.exception("mcp-router: LLM call failed — enabling all scoped servers")
            return None
        self._router_usage = (response.input_tokens, response.output_tokens)
        handles = self._parse_handles(response.text, set(catalog))
        log.info("mcp-router: selected %s of %s", sorted(handles), sorted(catalog))
        return handles

    def _router_task_summary(self) -> str:
        """Concise task description for the router, assembled from cheap,
        already-available signals (no extra I/O)."""
        parts = [f"Archetype: {self._archetype.name}", f"Goal: {self._archetype.goal}", f"Target: {self._cfg.target}"]
        if self._cfg.extra_user_instructions:
            parts.append(self._cfg.extra_user_instructions)
        for section in self._cfg.task_context_sections:
            if getattr(section, "is_empty", False):
                continue
            title = getattr(section, "title", "")
            body = (getattr(section, "body", "") or "")[:1000]
            parts.append(f"{title}: {body}")
        return "\n".join(parts)[:4000]

    @staticmethod
    def _parse_handles(text: str, known: set) -> set:
        """Extract known source ids from the router's reply. Prefers a JSON
        array; falls back to a lenient mention-scan. Empty set when nothing
        recognised (caller treats that as fail-open)."""
        import json
        import re

        match = re.search(r"\[.*?\]", text, re.S)
        if match:
            try:
                arr = json.loads(match.group(0))
                picked = {str(x) for x in arr} & known
                if picked:
                    return picked
            except (ValueError, TypeError):
                pass
        return {handle for handle in known if handle in text}

    def run(self) -> AgentRunResult:
        try:
            return self._run()
        finally:
            # MCP sessions hold subprocesses / open HTTP connections — tear
            # them down on every exit path (dry-run, error, ceiling, done).
            if self._mcp is not None:
                self._mcp.close()

    def _run(self) -> AgentRunResult:
        with log_context(company=self._cfg.company, task=self._cfg.task, agent=self._archetype.name):
            # Dry-run path: build the same prompts the LLM would see,
            # print them, and return. Don't gate on LLM creds (the
            # whole point is to validate the prompt rendering without
            # an LLM call) — but DO gate on JIT extractor / archetype
            # construction having succeeded, which happens at __init__.
            if self._cfg.dry_run:
                # Connect every scoped server (no router — routing would
                # spend tokens, and dry-run's contract is zero LLM calls)
                # so the report shows the full available tool menu.
                self._setup_mcp(route=False)
                return self._dry_run_report()
            if not self._llm.is_available():
                names = type(self._llm).required_env_vars()
                # Name the env vars the provider checks, and surface the
                # three remediation paths an operator can take. Joining
                # with " or " is correct for AnthropicLLM (oauth OR api
                # key) and also reads fine for single-key providers.
                needed = " or ".join(names) if names else "(see provider env_vars)"
                return AgentRunResult(
                    company=self._cfg.company,
                    task=self._cfg.task,
                    error=(
                        f"LLM ({self._llm.kind}) credentials missing — set {needed} "
                        f"via one of: (1) shell env, (2) `briar auth login` if a vendor "
                        f"acquirer exists, (3) hand-edit ~/.config/briar/secrets.env. "
                        f"If you expected a credential bootstrap to hydrate "
                        f"this, check the earlier `credential-bootstrap: … failed` log line."
                    ),
                )
            # Router pre-pass (Lever 4): pick which scoped servers to
            # connect for this task, then connect them. Must run after the
            # credentials gate (it calls the LLM) and before the system
            # prompt (whose context-source map reflects the connected set).
            self._setup_mcp(route=True)
            system = self._build_system_prompt()
            initial_user = self._build_initial_user_message()
            result = AgentRunResult(company=self._cfg.company, task=self._cfg.task)
            # Fold the router's token use into the run's accounting so the
            # cost summary tells the truth about the extra pre-pass call.
            result.input_tokens += self._router_usage[0]
            result.output_tokens += self._router_usage[1]
            log.info(
                "agent-start: archetype=%s llm=%s max_iter=%d workdir=%s",
                self._archetype.name,
                self._llm.kind,
                self._max_iterations,
                self._workdir,
            )
            log.debug("agent-system-prompt-bytes=%d initial-user-bytes=%d", len(system), len(initial_user))
            messages: List[Dict[str, Any]] = [{"role": "user", "content": initial_user}]
            for iteration in range(1, self._max_iterations + 1):
                result.iterations = iteration
                try:
                    response = self._llm.complete(
                        system=system,
                        messages=messages,
                        tools=self._tool_specs(),
                        max_tokens=self.DEFAULT_MAX_TOKENS_PER_TURN,
                    )
                except Exception:  # noqa: BLE001
                    log.exception("agent-failed: LLM raised on iteration %d", iteration)
                    result.error = "api: LLM call failed (see traceback in log)"
                    return result

                result.input_tokens += response.input_tokens
                result.output_tokens += response.output_tokens
                stop = response.stop_reason
                result.stop_reason = stop
                log.info(
                    "agent-turn iter=%d stop=%s tool_calls=%d in=%d out=%d",
                    iteration,
                    stop,
                    len(response.tool_calls),
                    response.input_tokens,
                    response.output_tokens,
                )
                if stop == StopReason.END_TURN:
                    result.final_text = response.text
                    log.info("agent-done iter=%d %s", iteration, result.cost_summary())
                    return result
                if stop != StopReason.TOOL_USE:
                    result.error = f"unexpected stop_reason={stop}"
                    log.warning("agent-stopped: %s", result.error)
                    return result
                tool_results = self._execute_all_tool_uses(response.tool_calls, result)
                messages.append(response.raw_assistant_message)
                messages.append({"role": "user", "content": tool_results})
            result.error = f"hit iteration ceiling ({self._max_iterations})"
            log.warning("agent-ceiling: %d iterations exhausted %s", self._max_iterations, result.cost_summary())
            return result

    def _build_system_prompt(self) -> str:
        persona = self._archetype.build_persona(self._cfg.target)
        from briar.iac.scaffold._knowledge import KnowledgeSplicer

        try:
            splicer = KnowledgeSplicer.from_store(self._cfg.knowledge_store, self._cfg.company)
            prologue = splicer.prologue(self._archetype)
        except Exception:  # noqa: BLE001
            log.exception("agent-system: KnowledgeSplicer failed — continuing without prologue")
            prologue = ""

        body = textwrap.dedent(
            f"""\
            You are: {persona['role']}.

            Goal: {persona['goal']}

            {persona['backstory']}

            ---
            Working directory: {self._workdir}
            All file operations (read_file, write_file, edit_file) and shell
            commands (bash) must operate inside this directory. The
            scheduler set the human git identity already; verify with
            `git config user.name` before your first commit.
            """
        )
        sections: List[str] = []
        if prologue:
            sections.append(prologue)
        # Task-scoped sections (ticket-context, pr-review-context, …)
        # render with their own `## <title>` heading + body, mirroring
        # the format the scheduled KnowledgeSplicer emits. The agent
        # treats both layers as one continuous context block.
        for section in self._cfg.task_context_sections:
            if getattr(section, "is_empty", False):
                continue
            sections.append(f"## {section.title}\n\n{section.body}")
        # Context-source routing map (Lever 2): when MCP servers are bound,
        # give the model an explicit menu of where each kind of context
        # lives + a selection policy, so it judges the most specific source
        # per sub-question instead of inferring from a flat tool list.
        routing = self._context_source_section()
        if routing:
            sections.append(routing)
        if not sections:
            return body
        return body + "\n\n" + "\n\n".join(sections)

    def _context_source_section(self) -> str:
        """Render the context-source map from the bound MCP tools, grouped
        by server with its `purpose`. Empty when no MCP servers are bound —
        non-MCP runs keep their existing prompt verbatim."""
        if not self._mcp_tools:
            return ""
        # Group tool names by server; the server name is segment 2 of the
        # `mcp__<server>__<tool>` convention (parsed so this works for any
        # tool object that follows the naming, not just McpTool).
        servers: Dict[str, Dict[str, Any]] = {}
        for tool in self._mcp_tools:
            parts = tool.name.split("__")
            server = parts[1] if len(parts) >= 3 else getattr(tool, "server", "mcp")
            info = servers.setdefault(server, {"purpose": "", "count": 0})
            info["count"] += 1
            purpose = getattr(tool, "purpose", "")
            if purpose and not info["purpose"]:
                info["purpose"] = purpose

        lines = [
            "## Context sources — pick the most specific one per sub-question",
            "",
            "Before each action, name the information you need, then use the source whose purpose matches it:",
            "",
            "- Local repository (code, configs, tests, history) — `read_file`, `bash`",
        ]
        for server, info in servers.items():
            purpose = info["purpose"] or f"the {server} MCP server"
            lines.append(f"- {purpose} — `mcp__{server}__*`")
        lines.append("")
        lines.append(
            "Prefer local repository context before remote sources. Don't call a tool whose "
            "purpose doesn't match what you need; when sources overlap, choose the most specific."
        )
        return "\n".join(lines)

    def _build_initial_user_message(self) -> str:
        intro = textwrap.dedent(
            f"""\
            Run the {self._archetype.name} workflow for company {self._cfg.company!r}
            in repo {self._cfg.target!r}. The working directory at {self._workdir}
            is a clean git worktree on the PR branch you should fix.

            Procedure (follow exactly):
              1. `bash`: `cd <workdir> && gh pr view <N> --json reviewDecision,headRefName,statusCheckRollup`
              2. Skip the PR if reviewDecision=APPROVED AND every required check is green AND every
                 open review thread has only positive comments. Report 'skipped' and end.
              3. Otherwise, list every open inline review-thread comment + every PR-level issue comment.
              4. For each thread requesting a code change: apply the smallest correct fix via edit_file,
                 then commit (one commit per thread; subject ≤72 chars; body cites the comment id).
              5. After all fixes, push fast-forward to the PR's branch.
              6. Reply to each thread you addressed via `gh api .../comments/{{id}}/replies` with
                 one sentence citing the commit SHA.
              7. Report a short summary of commits made + threads replied to. Then stop.

            Strict constraints:
              - NEVER --force, --amend, rebase, squash, or filter-branch.
              - NEVER commit as a bot identity. Run `git config user.name` first to verify it's a human.
              - NEVER touch files outside the diff already under review unless the fix needs it.
              - If a thread is subjective ('did you consider X?'), reply with clarification, no commit.
              - If you cannot resolve a thread safely, leave a comment explaining why and skip it.
            """
        )
        if self._cfg.extra_user_instructions:
            intro = intro + "\n\nAdditional instructions:\n" + self._cfg.extra_user_instructions
        return intro

    def _tool_specs(self) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = [
            {"name": self._bash.name, "description": self._bash.description, "input_schema": self._bash.INPUT_SCHEMA},
            {"name": self._read.name, "description": self._read.description, "input_schema": self._read.INPUT_SCHEMA},
            {"name": self._write.name, "description": self._write.description, "input_schema": self._write.INPUT_SCHEMA},
            {"name": self._edit.name, "description": self._edit.description, "input_schema": self._edit.INPUT_SCHEMA},
        ]
        if self._send is not None:
            # Append the channel list to the description so the LLM sees
            # the actual handles at agent-start time.
            channels = self._send.channels()
            description = self._send.description + (f"\n\nAvailable channels: {', '.join(channels)}" if channels else "")
            specs.append({"name": self._send.name, "description": description, "input_schema": self._send.INPUT_SCHEMA})
        for tool in self._mcp_tools:
            specs.append({"name": tool.name, "description": tool.description, "input_schema": tool.INPUT_SCHEMA})
        return specs

    def _dry_run_report(self) -> AgentRunResult:
        """Build the same prompts the LLM would see, print them to
        stdout, and return without a single API call.

        Goal: let the operator validate the JIT context wiring
        (ticket-context / pr-review-context renders correctly, the
        archetype's consumes order is what they expect, the tool
        specs look right) WITHOUT spending tokens. Skips both LLM
        availability checks and the iteration loop entirely."""
        system = self._build_system_prompt()
        initial_user = self._build_initial_user_message()
        tools = self._tool_specs()

        sep = "=" * 78
        print(sep)
        print(f"DRY RUN — archetype={self._archetype.name} task={self._cfg.task} target={self._cfg.target}")
        print(f"company={self._cfg.company} llm={self._llm.kind}  (LLM call SKIPPED)")
        print(sep)
        print()
        print("─── SYSTEM PROMPT ───────────────────────────────────────────────────────")
        print(system)
        print()
        print("─── INITIAL USER MESSAGE ────────────────────────────────────────────────")
        print(initial_user)
        print()
        print("─── TOOLS BOUND ─────────────────────────────────────────────────────────")
        for t in tools:
            print(f"  - {t['name']}: {t['description']}")
        print()
        print("─── TASK-SCOPED SECTIONS (count + titles) ───────────────────────────────")
        if self._cfg.task_context_sections:
            for section in self._cfg.task_context_sections:
                title = getattr(section, "title", "(no title)")
                body_bytes = len(getattr(section, "body", "") or "")
                print(f"  - {title}  ({body_bytes} bytes)")
        else:
            print("  (none — pass --ticket-key / --pr to populate)")
        print()
        print(sep)
        print(f"system_prompt_bytes={len(system)}  initial_user_bytes={len(initial_user)}  tool_count={len(tools)}")
        print(sep)

        return AgentRunResult(
            company=self._cfg.company,
            task=self._cfg.task,
            iterations=0,
            stop_reason=StopReason.DRY_RUN,
            final_text="(dry run — no LLM call)",
        )

    def _execute_all_tool_uses(self, tool_calls: List[LLMToolCall], result: AgentRunResult) -> List[Dict[str, Any]]:
        """Dispatch each tool call from the LLM, then format the result
        in the provider's echo-back shape via `LLMProvider.format_tool_result`.
        Keeps the runner free of any vendor-specific result shape."""
        tool_results: List[Dict[str, Any]] = []
        for call in tool_calls:
            result.tool_calls += 1
            outcome = self._dispatch_tool(call.name, call.arguments, result)
            tool_results.append(
                self._llm.format_tool_result(
                    tool_call_id=call.id,
                    output=outcome["content"],
                    is_error=outcome["is_error"],
                )
            )
        return tool_results

    def _dispatch_tool(self, name: str, raw_input: Any, result: AgentRunResult) -> Dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return {"content": f"unknown tool {name!r}", "is_error": True}
        try:
            out = tool.run(**raw_input)
            # `git commit` prints a sha line on bash stdout — capture it.
            if tool is self._bash:
                self._record_commit_if_any(out, result)
            return {"content": out, "is_error": False}
        except ToolError as exc:
            log.warning("tool %s error: %s", name, exc)
            return {"content": str(exc), "is_error": True}
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s raised unexpectedly", name)
            return {"content": f"unexpected {type(exc).__name__}: {exc}", "is_error": True}

    @staticmethod
    def _record_commit_if_any(bash_output: str, result: AgentRunResult) -> None:
        # `git commit` prints `[branch sha] subject` on the first line of stdout.
        import re

        match = re.search(r"\[[^\]]+\s+([a-f0-9]{7,40})\]", bash_output)
        if match:
            result.commits.append(match.group(1))
