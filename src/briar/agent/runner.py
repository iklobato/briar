"""Agent runner — Anthropic-API-driven tool-use loop.

Loads the pr-fixer archetype's system prompt, splices in the company's
knowledge, and runs `client.messages.create` in a loop until the model
returns `end_turn` (or we hit guardrails). Tool calls dispatch to the
`BashTool` / `ReadFileTool` / `WriteFileTool` / `EditFileTool` primitives
in `briar.agent.tools`.
"""

from __future__ import annotations

import logging
import os
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from briar.agent.tools import BashTool, EditFileTool, ReadFileTool, ToolError, WriteFileTool
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


class AgentRunner:
    """One-shot agent execution against a single (company, task) target.

    Loads the archetype's persona + the spliced knowledge for the company,
    builds the system prompt, then drives the Anthropic API tool-use loop
    until the model is done or we hit a guardrail. All tool side effects
    are confined to the worktree the caller hands us.
    """

    DEFAULT_MODEL = "claude-sonnet-4-5"
    DEFAULT_MAX_ITERATIONS = 30
    DEFAULT_MAX_TOKENS_PER_TURN = 8_000

    def __init__(
        self,
        *,
        company: str,
        task: str,
        archetype_name: str,
        workdir: Path,
        knowledge_store: Any,
        target: str,
        api_key: str = "",
        model: str = "",
        max_iterations: int = 0,
        extra_user_instructions: str = "",
    ) -> None:
        self._company = company
        self._task = task
        self._workdir = workdir.resolve()
        self._archetype = ARCHETYPES[archetype_name]
        self._store = knowledge_store
        self._target = target
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model or self.DEFAULT_MODEL
        self._max_iterations = max_iterations or self.DEFAULT_MAX_ITERATIONS
        self._extra = extra_user_instructions
        # Tools share the same root list so the agent can read/write
        # inside the worktree but nowhere else.
        roots = [self._workdir]
        self._bash = BashTool(base_cwd=self._workdir)
        self._read = ReadFileTool(allowed_roots=roots)
        self._write = WriteFileTool(allowed_roots=roots)
        self._edit = EditFileTool(allowed_roots=roots)

    def run(self) -> AgentRunResult:
        with log_context(company=self._company, task=self._task, agent=self._archetype.name):
            if not self._api_key:
                return AgentRunResult(
                    company=self._company,
                    task=self._task,
                    error="ANTHROPIC_API_KEY missing — set it in /etc/briar/secrets.env or env",
                )
            import anthropic

            client = anthropic.Anthropic(api_key=self._api_key)
            system = self._build_system_prompt()
            initial_user = self._build_initial_user_message()
            log.info(
                "agent-start: archetype=%s model=%s max_iter=%d workdir=%s",
                self._archetype.name,
                self._model,
                self._max_iterations,
                self._workdir,
            )
            log.debug("agent-system-prompt-bytes=%d initial-user-bytes=%d", len(system), len(initial_user))
            messages: List[Dict[str, Any]] = [{"role": "user", "content": initial_user}]
            result = AgentRunResult(company=self._company, task=self._task)
            for iteration in range(1, self._max_iterations + 1):
                result.iterations = iteration
                try:
                    response = client.messages.create(
                        model=self._model,
                        max_tokens=self.DEFAULT_MAX_TOKENS_PER_TURN,
                        system=system,
                        tools=self._tool_specs(),
                        messages=messages,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.exception("agent-failed: anthropic API raised on iteration %d", iteration)
                    result.error = f"api: {exc}"
                    return result
                if response.usage is not None:
                    result.input_tokens += response.usage.input_tokens
                    result.output_tokens += response.usage.output_tokens
                stop = getattr(response, "stop_reason", "")
                result.stop_reason = stop
                log.info(
                    "agent-turn iter=%d stop=%s blocks=%d in=%d out=%d",
                    iteration,
                    stop,
                    len(response.content),
                    response.usage.input_tokens if response.usage else 0,
                    response.usage.output_tokens if response.usage else 0,
                )
                if stop == "end_turn":
                    result.final_text = self._extract_text(response.content)
                    log.info("agent-done iter=%d %s", iteration, result.cost_summary())
                    return result
                if stop != "tool_use":
                    result.error = f"unexpected stop_reason={stop}"
                    log.warning("agent-stopped: %s", result.error)
                    return result
                tool_results = self._execute_all_tool_uses(response.content, result)
                messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})
                messages.append({"role": "user", "content": tool_results})
            result.error = f"hit iteration ceiling ({self._max_iterations})"
            log.warning("agent-ceiling: %d iterations exhausted %s", self._max_iterations, result.cost_summary())
            return result

    def _build_system_prompt(self) -> str:
        persona = self._archetype.build_persona(self._target)
        from briar.iac.scaffold._knowledge import KnowledgeSplicer

        try:
            splicer = KnowledgeSplicer(self._store, self._company)
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
        return body + ("\n\n" + prologue if prologue else "")

    def _build_initial_user_message(self) -> str:
        intro = textwrap.dedent(
            f"""\
            Run the {self._archetype.name} workflow for company {self._company!r}
            in repo {self._target!r}. The working directory at {self._workdir}
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
        if self._extra:
            intro = intro + "\n\nAdditional instructions:\n" + self._extra
        return intro

    def _tool_specs(self) -> List[Dict[str, Any]]:
        return [
            {"name": self._bash.name, "description": self._bash.description, "input_schema": self._bash.INPUT_SCHEMA},
            {"name": self._read.name, "description": self._read.description, "input_schema": self._read.INPUT_SCHEMA},
            {"name": self._write.name, "description": self._write.description, "input_schema": self._write.INPUT_SCHEMA},
            {"name": self._edit.name, "description": self._edit.description, "input_schema": self._edit.INPUT_SCHEMA},
        ]

    def _execute_all_tool_uses(self, blocks: Any, result: AgentRunResult) -> List[Dict[str, Any]]:
        tool_results: List[Dict[str, Any]] = []
        for block in blocks:
            if getattr(block, "type", "") != "tool_use":
                continue
            result.tool_calls += 1
            tool_result = self._dispatch_tool(block.name, block.input, result)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": tool_result["content"],
                    "is_error": tool_result["is_error"],
                }
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

    @staticmethod
    def _extract_text(blocks: Any) -> str:
        parts: List[str] = []
        for block in blocks:
            if getattr(block, "type", "") == "text":
                parts.append(block.text)
        return "\n".join(parts)
