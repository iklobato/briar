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
        self._send = (
            SendMessageTool(messages=dict(config.messages), company=config.company)
            if config.messages
            else None
        )

    def run(self) -> AgentRunResult:
        with log_context(company=self._cfg.company, task=self._cfg.task, agent=self._archetype.name):
            # Dry-run path: build the same prompts the LLM would see,
            # print them, and return. Don't gate on LLM creds (the
            # whole point is to validate the prompt rendering without
            # an LLM call) — but DO gate on JIT extractor / archetype
            # construction having succeeded, which happens at __init__.
            if self._cfg.dry_run:
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
                        f"If you expected a credential bootstrap (Infisical, …) to hydrate "
                        f"this, check the earlier `credential-bootstrap: … failed` log line."
                    ),
                )
            system = self._build_system_prompt()
            initial_user = self._build_initial_user_message()
            log.info(
                "agent-start: archetype=%s llm=%s max_iter=%d workdir=%s",
                self._archetype.name,
                self._llm.kind,
                self._max_iterations,
                self._workdir,
            )
            log.debug("agent-system-prompt-bytes=%d initial-user-bytes=%d", len(system), len(initial_user))
            messages: List[Dict[str, Any]] = [{"role": "user", "content": initial_user}]
            result = AgentRunResult(company=self._cfg.company, task=self._cfg.task)
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
        if not sections:
            return body
        return body + "\n\n" + "\n\n".join(sections)

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
        try:
            if name == self._bash.name:
                out = self._bash.run(**raw_input)
                self._record_commit_if_any(out, result)
                return {"content": out, "is_error": False}
            if name == self._read.name:
                return {"content": self._read.run(**raw_input), "is_error": False}
            if name == self._write.name:
                return {"content": self._write.run(**raw_input), "is_error": False}
            if name == self._edit.name:
                return {"content": self._edit.run(**raw_input), "is_error": False}
            if self._send is not None and name == self._send.name:
                return {"content": self._send.run(**raw_input), "is_error": False}
            return {"content": f"unknown tool {name!r}", "is_error": True}
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

