"""The agent of `briar serve`: the Claude Agent SDK (S1), behind `AgentRunner` so the
routines are tested with a fake.

The agent edits files only inside the worktree of its task, reads with its tools only
there and in the knowledge folder of the company, and runs only the bash commands of the
allowlist of the company (S25, A13), inside the sandbox of the SDK: no network but the
`allowed_domains` of the company (V12, V13). Push, PR, comments and ticket changes are
briar code (D10). Each call gets the money left in the task (`max_usd`, S23) as
`max_budget_usd`."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple


class Access(Enum):
    """What an agent call may do: read only (the plan, the self-review), or edit too."""

    READ = "read"
    EDIT = "edit"


@dataclass(frozen=True)
class AgentRequest:
    """One agent call: the task text, the role text, where it works, and its limits."""

    prompt: str
    system_prompt: str
    cwd: Path
    max_usd: float
    # A JSON schema the answer must match (the plan, the self-review); empty: free text.
    schema: Mapping[str, Any] = field(default_factory=dict)
    access: Access = Access.EDIT


@dataclass(frozen=True)
class AgentAnswer:
    """What one agent call gave: its text or structured answer, its cost, and whether it failed."""

    text: str
    structured: Mapping[str, Any]
    cost_usd: float
    failed: bool = False
    reason: str = ""


class AgentRunner(Protocol):
    async def ask(self, request: AgentRequest) -> AgentAnswer: ...


class BashGuard:
    """Decides each tool call of the agent: edits stay in the worktree, bash runs only
    the allowlisted programs and never a forbidden token (from the 1.x bash tool)."""

    DEFAULT_VERBS: ClassVar[Tuple[str, ...]] = (
        "git", "ls", "cat", "head", "tail", "grep", "rg", "find", "wc", "pwd", "echo", "cd", "python", "python3",
        "pytest", "ruff", "black", "mypy", "npm", "node", "make", "tee", "sort", "uniq",
    )  # fmt: skip
    FORBIDDEN: ClassVar[Tuple[str, ...]] = (
        "rm -rf", "sudo", "su ", "ssh ", "scp ", "curl", "wget", "shutdown", "reboot", "chmod 777", "mkfs", "dd if=",
        "--force", "-f origin", "--amend", "rebase", "squash", "filter-branch", "reset --hard", "git push", "gh ",
    )  # fmt: skip
    READ_TOOLS: ClassVar[Tuple[str, ...]] = ("Read", "Glob", "Grep", "LS")
    FREE_TOOLS: ClassVar[Tuple[str, ...]] = ("TodoWrite",)
    PATH_KEYS: ClassVar[Tuple[str, ...]] = ("file_path", "path", "notebook_path")
    GLOB_CHARS: ClassVar[str] = "*?["
    EDIT_TOOLS: ClassVar[Tuple[str, ...]] = ("Edit", "MultiEdit", "Write", "NotebookEdit")
    SEPARATORS: ClassVar[str] = ";|\n"
    REDIRECT: ClassVar["re.Pattern[str]"] = re.compile(r"[0-9]*[<>]&[0-9-]*|&>>?")
    ASSIGNMENT: ClassVar["re.Pattern[str]"] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    HEREDOC: ClassVar["re.Pattern[str]"] = re.compile(r"<<-?\s*(?P<quote>['\"]?)(?P<end>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)")
    # Shell builtins with no effect outside the shell; the verify commands of a runbook use them.
    BUILTINS: ClassVar[Tuple[str, ...]] = ("[", "[[", "test", "true", "false", "exit")

    def __init__(self, worktree: Path, verbs: Sequence[str], readable: Sequence[Path]) -> None:
        self._worktree = worktree
        self._verbs = verbs
        self._readable = readable

    def refusal(self, tool: str, tool_input: Mapping[str, Any]) -> str:
        """Why the call is refused, or "" to allow it."""
        if tool in self.FREE_TOOLS:
            return ""
        if tool in self.READ_TOOLS:
            return self._unreadable(tool, tool_input)
        if tool in self.EDIT_TOOLS:
            return self._outside(str(tool_input.get("file_path") or tool_input.get("notebook_path") or ""))
        if tool == "Bash":
            return self._bash(str(tool_input.get("command", "")))
        return f"{tool} is not allowed in briar serve"

    def _outside(self, path: str) -> str:
        inside = self._within(self._worktree / path, [self._worktree])
        return "" if inside else f"edits stay inside the worktree {self._worktree}: {path} is outside"

    def _unreadable(self, tool: str, tool_input: Mapping[str, Any]) -> str:
        """V12: the read tools stay in the worktree and the readable folders. A Glob pattern
        is checked up to its first wildcard (`../../**` climbs out too)."""
        base = self._worktree / Path(str(next((tool_input[key] for key in self.PATH_KEYS if tool_input.get(key)), "."))).expanduser()
        targets = [base]
        if tool == "Glob":
            targets.append(base / self._fixed_part(str(tool_input.get("pattern", ""))))
        outside = [target.expanduser().resolve() for target in targets if not self._within(target, [self._worktree, *self._readable])]
        return f"reads stay inside the worktree and the knowledge folder: {outside[0]} is outside" if outside else ""

    def _fixed_part(self, pattern: str) -> Path:
        parts = Path(pattern).expanduser().parts
        fixed = [part for index, part in enumerate(parts) if not any(char in self.GLOB_CHARS for char in "".join(parts[: index + 1]))]
        return Path(*fixed) if fixed else Path(".")

    def _within(self, path: Path, roots: Sequence[Path]) -> bool:
        target = path.expanduser().resolve()
        return any(target == root.resolve() or root.resolve() in target.parents for root in roots)

    def _bash(self, command: str) -> str:
        lowered = command.lower()
        # A token starts a word: `gh ` is in "high " (a real run refused a grep for it).
        forbidden = [token for token in self.FORBIDDEN if re.search(rf"(?<!\w){re.escape(token)}", lowered)]
        if forbidden:
            return f"forbidden in briar serve: {forbidden[0]!r} (push, PR and comments are done by briar)"
        unknown = [verb for verb in self._verbs_of(self._without_heredocs(command)) if not self._allowed(verb)]
        return f"not in the bash allowlist of this company: {unknown[0]!r}" if unknown else ""

    def _allowed(self, verb: str) -> bool:
        """An allowed program, a builtin, or an allowed program of the project by a relative
        path (`.venv/bin/python`); never one by an absolute path or out of the worktree."""
        allowed = self._verbs or self.DEFAULT_VERBS
        path = PurePosixPath(verb)
        project = len(path.parts) > 1 and not path.is_absolute() and ".." not in path.parts and not verb.startswith("~")
        return verb in allowed or verb in self.BUILTINS or (project and path.name in allowed)

    def _without_heredocs(self, command: str) -> str:
        """A real run: the lines of `python3 - <<'EOF' ... EOF` read as commands. The body
        of a heredoc is data for its program."""
        kept: List[str] = []
        ends: List[str] = []
        for line in command.split("\n"):
            if ends and line.strip() == ends[0]:
                ends.pop(0)
                continue
            if ends:
                continue
            kept.append(line)
            ends.extend(match.group("end") for match in self.HEREDOC.finditer(line))
        return "\n".join(kept)

    def _verbs_of(self, command: str) -> List[str]:
        """The program of each command of the line (`;`, `|`, `&&`, `(` and `)` start a new
        one). Real runs showed three things that are no command: a redirection (`2>&1` read as
        `&&`), a subshell paren (`(ruff`) and `NAME=value` before the program."""
        # shlex reads a new line as a space: before this, only the first line was checked.
        lines = self.REDIRECT.sub(" ", command).replace("\n", ";")
        lexer = shlex.shlex(lines, posix=True, punctuation_chars=self.SEPARATORS + "&()")
        lexer.whitespace_split = True
        verbs: List[str] = []
        starting = True
        for word in lexer:
            if set(word) <= set(self.SEPARATORS + "&()"):
                starting = True
                continue
            if starting and not self.ASSIGNMENT.match(word):
                verbs.append(word)
                starting = False
        return verbs


class ToolGate:
    """The `can_use_tool` callback of one agent call: the guard, and no edit at all when
    the call is read only (the plan, the self-review)."""

    def __init__(self, guard: BashGuard, access: Access) -> None:
        self._guard = guard
        self._access = access

    def refusal(self, tool: str, tool_input: Mapping[str, Any]) -> str:
        if self._access is Access.READ and tool in BashGuard.EDIT_TOOLS:
            return "this step only reads: write the answer, do not edit files"
        return self._guard.refusal(tool, tool_input)

    async def __call__(self, tool: str, tool_input: Dict[str, Any], context: Any) -> Any:
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        refusal = self.refusal(tool, tool_input)
        return PermissionResultDeny(message=refusal) if refusal else PermissionResultAllow()


class ClaudeAgent:
    """`AgentRunner` on the Claude Agent SDK, with the model of the company (S5)."""

    # With no list the CLI sends all its tools and the skill and agent lists: about 39k input
    # tokens per call, even when `ToolGate` refuses every one of them (validation run, 2026-11-12).
    TOOLS: ClassVar[Mapping[Access, Tuple[str, ...]]] = {
        Access.READ: (*BashGuard.READ_TOOLS, *BashGuard.FREE_TOOLS, "Bash"),
        Access.EDIT: (*BashGuard.READ_TOOLS, *BashGuard.FREE_TOOLS, "Bash", *BashGuard.EDIT_TOOLS),
    }

    def __init__(self, model: str, guard: BashGuard, domains: Sequence[str]) -> None:
        self._model = model
        self._guard = guard
        self._domains = domains

    async def ask(self, request: AgentRequest) -> AgentAnswer:
        from claude_agent_sdk import ClaudeSDKError

        found: List[Any] = []
        try:
            await self._collect(request, found)
        except ClaudeSDKError as error:
            # An error result is yielded, then the CLI exits non-zero and the SDK raises
            # (`ResultError`, checked 2026-11-11 with a refused API key).
            return self._failed(found[-1] if found else None, error)
        return self._answer(found[-1] if found else None)

    async def _collect(self, request: AgentRequest, found: List[Any]) -> None:
        """Run the query and keep its result messages in `found`."""
        from claude_agent_sdk import ResultMessage, query

        async for message in query(prompt=request.prompt, options=self._options(request)):
            if isinstance(message, ResultMessage):
                found.append(message)

    def _failed(self, message: Optional[Any], error: Exception) -> AgentAnswer:
        subtype = getattr(error, "subtype", None)
        reason = subtype if subtype and subtype != "success" else str(error)
        cost = message.total_cost_usd if message is not None else (getattr(error, "data", None) or {}).get("total_cost_usd")
        return AgentAnswer("", {}, float(cost or 0.0), failed=True, reason=reason)

    def _options(self, request: AgentRequest) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions

        options: Dict[str, Any] = {
            "model": self._model,
            "cwd": str(request.cwd),
            "system_prompt": request.system_prompt,
            "max_budget_usd": request.max_usd,
            "permission_mode": "default",
            "can_use_tool": ToolGate(self._guard, request.access),
            "setting_sources": [],
            "tools": list(self.TOOLS[request.access]),
            # Else the MCP servers of the account of the CLI (claude.ai connectors) come too.
            "strict_mcp_config": True,
            "sandbox": self._sandbox(),
        }
        if request.schema:
            options["output_format"] = {"type": "json_schema", "schema": dict(request.schema)}
        return ClaudeAgentOptions(**options)

    def _sandbox(self) -> Dict[str, Any]:
        """V12, V13: bash runs sandboxed with no way out of it, and each command still goes
        through `can_use_tool` (the allowlist) instead of the auto-allow of a sandbox."""
        network = {"allowedDomains": list(self._domains)}
        return {"enabled": True, "autoAllowBashIfSandboxed": False, "allowUnsandboxedCommands": False, "network": network}

    def _answer(self, message: Optional[Any]) -> AgentAnswer:
        if message is None:
            return AgentAnswer("", {}, 0.0, failed=True, reason="the agent gave no result")
        structured = message.structured_output if isinstance(message.structured_output, dict) else {}
        reason = message.subtype if message.is_error else ""
        return AgentAnswer(message.result or "", structured, float(message.total_cost_usd or 0.0), message.is_error, reason)


__all__ = ["Access", "AgentAnswer", "AgentRequest", "AgentRunner", "BashGuard", "ClaudeAgent", "ToolGate"]
