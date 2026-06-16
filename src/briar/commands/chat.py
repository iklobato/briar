"""`briar chat` — an interactive assistant that drives briar via its own MCP server.

Connects to ``briar mcp serve --transport stdio`` (spawned as a subprocess
through the existing `McpClientManager`), binds its tools as ``mcp__briar__*``,
and runs an LLM tool-use loop so you can ask briar to inspect and change its
own knowledge, config, and extraction in natural language.

Human-in-the-loop gating: the model never self-confirms a mutation. The chat
client strips any ``confirm`` the model passes, runs the gated tool as a
dry-run first, shows you the preview, and only re-invokes it with
``confirm=true`` after you approve at the terminal.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from briar.agent._enums import StopReason
from briar.agent._llm import LLMProvider, LLMToolCall
from briar.commands.base import Command, confirm
from briar.storage import KNOWLEDGE_STORE_NAMES

_SYSTEM = (
    "You are briar's control assistant. You operate briar through its own tools "
    "(named mcp__briar__*): inspect and edit knowledge blobs, read and change the "
    "runbook config, and run extractors. Prefer the most specific tool. When a tool "
    "is gated, the user is asked to approve before it actually runs — so just call "
    "the tool; do not pass `confirm` yourself. Be concise."
)

_MAX_TOOL_ITERS = 25
_MAX_TOKENS = 4_000


def _dry_run_summary(output: str) -> Optional[str]:
    """If a tool's output is a gated dry-run result, return its summary;
    otherwise None. Reads (lists/strings) and executed results return None."""
    try:
        parsed = json.loads(output)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, dict) and parsed.get("mode") == "dry_run" and parsed.get("executed") is False:
        return str(parsed.get("summary", "(no summary)"))
    return None


class ChatSession:
    """One LLM-driven tool-use loop over briar's MCP tools.

    Decoupled from transport and I/O so it is unit-testable: inject the LLM,
    the tool list, the confirm callback, and an output sink."""

    def __init__(
        self,
        llm: LLMProvider,
        tools: List[Any],
        *,
        confirm_fn: Callable[[str], bool] = confirm,
        out: Callable[[str], None] = print,
        system: str = _SYSTEM,
    ) -> None:
        self._llm = llm
        self._tools = {t.name: t for t in tools}
        self._confirm = confirm_fn
        self._out = out
        self._system = system

    def _tool_specs(self) -> List[Dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "input_schema": t.INPUT_SCHEMA} for t in self._tools.values()]

    def _dispatch(self, call: LLMToolCall) -> Tuple[str, bool]:
        """Run one tool call, enforcing the human gate. Returns (content, is_error)."""
        from briar.agent.tools import ToolError

        tool = self._tools.get(call.name)
        if tool is None:
            return f"unknown tool {call.name!r}", True
        # Never let the model self-confirm: drop it and gate through the human.
        args = {k: v for k, v in dict(call.arguments).items() if k != "confirm"}
        try:
            output = tool.run(**args)
            summary = _dry_run_summary(output)
            if summary is not None:
                if self._confirm(f"\n  ⚠  {summary}\n     Execute? [y/N] "):
                    output = tool.run(**{**args, "confirm": True})
                else:
                    output = json.dumps({"declined": True, "summary": summary})
            return output, False
        except ToolError as exc:
            return str(exc), True

    def ask(self, user_text: str) -> str:
        """Drive the tool-use loop for one user turn; return the final text."""
        messages: List[Dict[str, Any]] = [{"role": "user", "content": user_text}]
        for _ in range(_MAX_TOOL_ITERS):
            response = self._llm.complete(system=self._system, messages=messages, tools=self._tool_specs(), max_tokens=_MAX_TOKENS)
            if response.stop_reason == StopReason.END_TURN:
                return response.text
            if response.stop_reason != StopReason.TOOL_USE:
                return response.text or f"(stopped: {response.stop_reason})"
            tool_results = []
            for call in response.tool_calls:
                content, is_error = self._dispatch(call)
                tool_results.append(self._llm.format_tool_result(tool_call_id=call.id, output=content, is_error=is_error))
            messages.append(response.raw_assistant_message)
            messages.append({"role": "user", "content": tool_results})
        return "(hit tool-iteration limit without a final answer)"


class CommandChat(Command):
    name = "chat"
    help = "Interactive assistant that drives briar via its own MCP server."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        from briar.agent._llms import LLMRegistry

        parser.add_argument("--llm", default="anthropic", choices=list(LLMRegistry.kinds()), help="LLM provider (default: anthropic)")
        parser.add_argument("--model", default="", help="Override the provider's default model")
        parser.add_argument("--store", default="file", choices=list(KNOWLEDGE_STORE_NAMES), help="Knowledge store backend (default: file)")
        parser.add_argument("--root", default="./knowledge", help="Local knowledge file root")
        parser.add_argument("--runbook", default="", help="Runbook YAML path for config tools (omit to disable them)")

    def run(self, args: argparse.Namespace) -> int:
        from briar.agent._llms import make_llm
        from briar.iac.runbook.models import McpServerBinding
        from briar.mcp import McpClientManager

        llm = make_llm(args.llm, model=args.model)
        if not llm.is_available():
            needed = " or ".join(type(llm).required_env_vars()) or "(see provider env vars)"
            print(f"error: LLM ({llm.kind}) credentials missing — set {needed}", file=sys.stderr)
            return 1

        manager = McpClientManager({"briar": self._server_binding(args, McpServerBinding)}, connect_timeout_s=60.0)
        try:
            tools = manager.start()
            if not tools:
                print("error: briar MCP server exposed no tools (did it fail to start?)", file=sys.stderr)
                return 1
            session = ChatSession(llm, tools)
            self._repl(session)
        finally:
            manager.close()
        return 0

    @staticmethod
    def _server_binding(args: argparse.Namespace, binding_cls: type) -> Any:
        serve_args = ["-m", "briar", "mcp", "serve", "--transport", "stdio", "--store", args.store, "--root", args.root]
        if args.runbook:
            serve_args += ["--runbook", args.runbook]
        # Forward PYTHONPATH (by env-var NAME) so a `PYTHONPATH=src` dev run
        # reaches the child; the child re-hydrates credentials via briar's own
        # bootstrap on startup, so provider-backed tools still work.
        return binding_cls(transport="stdio", command=sys.executable, args=serve_args, env={"PYTHONPATH": "PYTHONPATH"})

    def _repl(self, session: ChatSession) -> None:
        print("briar chat — ask me to inspect or change briar. Ctrl-D or /exit to quit.\n")
        while True:
            try:
                line = input("you> ").strip()
            except EOFError:
                print()
                return
            if not line:
                continue
            if line in {"/exit", "/quit"}:
                return
            print(f"\nbriar> {session.ask(line)}\n")
