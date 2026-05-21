"""Agent tools — small, audited primitives the LLM can call.

Every tool returns a string (stdout / file content / error message); the
agent loop wraps that in a `tool_result` block. Inputs are validated
against tight allowlists before execution — the model cannot run
arbitrary shell, only the verbs we declare safe.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


log = logging.getLogger(__name__)


# Bash commands the agent is allowed to run. Anchored at the start of the
# command (after `shlex.split`). Anything matching `_FORBIDDEN_TOKENS`
# anywhere in the line is rejected even if the leading verb passed.
_ALLOWED_VERBS = {
    "git",
    "gh",
    "ls",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "wc",
    "pwd",
    "echo",
    "cd",  # only as a prefix, e.g. `cd worktree && git status`
    "python",
    "python3",
    "pytest",
    "ruff",
    "black",
    "mypy",
    "npm",
    "node",
    "make",
    "tee",
    "sort",
    "uniq",
}

_FORBIDDEN_TOKENS = (
    "rm -rf",
    "sudo",
    "su ",
    "ssh ",
    "scp ",
    "curl",
    "wget",
    " ;rm",
    "&&rm",
    "|rm ",
    "shutdown",
    "reboot",
    "kill -9 1",
    "chmod 777",
    "/dev/null > /dev",
    ">/dev/sda",
    "mkfs",
    "dd if=",
    # No force-push / amend / squash by mandate
    "--force",
    "-f origin",
    "--amend",
    "rebase",
    "squash",
    "filter-branch",
    "reset --hard",
)


@dataclass
class ToolError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class BashTool:
    name = "bash"
    description = "Run a shell command. Allowlisted verbs only: git, gh, ls, cat, head, tail, grep, find, wc, python/pytest/ruff/black/mypy, npm/node, make. NEVER --force, --amend, rebase, squash, rm -rf, sudo. Returns stdout+stderr. Non-zero exit becomes a tool_result with the error visible to you."

    INPUT_SCHEMA: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run. Pipes and && chaining are fine; forbidden tokens are rejected.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the command. Should be the PR worktree for git operations.",
            },
            "timeout_s": {
                "type": "integer",
                "description": "Max seconds before the process is killed. Default 60, hard ceiling 300.",
            },
        },
        "required": ["command", "cwd"],
    }

    def __init__(self, base_cwd: Path) -> None:
        self._base = base_cwd

    def run(self, command: str, cwd: str, timeout_s: int = 60) -> str:
        if timeout_s > 300:
            timeout_s = 300
        self._validate(command)
        path = self._resolve_cwd(cwd)
        log.info("bash-tool: cwd=%s cmd=%s", path, command[:120])
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        out = proc.stdout.rstrip()
        err = proc.stderr.rstrip()
        body_parts: List[str] = [f"$ {command}", f"(exit {proc.returncode}, cwd={path})"]
        if out:
            body_parts.append(out)
        if err:
            body_parts.append("STDERR:")
            body_parts.append(err)
        return "\n".join(body_parts)

    @classmethod
    def _validate(cls, command: str) -> None:
        lowered = command.lower()
        for tok in _FORBIDDEN_TOKENS:
            if tok in lowered:
                raise ToolError(f"bash-tool rejected: forbidden token {tok!r} in command")
        # Parse the leading verb of every &&-chained chunk and confirm
        # each is allowlisted. We can't shlex.split the whole thing —
        # `&&` and `|` are operators not args — so split on those first.
        chunks = re.split(r"\s*(?:&&|\|\||\||;)\s*", command)
        for chunk in chunks:
            if not chunk.strip():
                continue
            try:
                parts = shlex.split(chunk)
            except ValueError as exc:
                raise ToolError(f"bash-tool rejected: cannot parse shell tokens — {exc}") from exc
            if not parts:
                continue
            verb = parts[0]
            if verb not in _ALLOWED_VERBS:
                raise ToolError(f"bash-tool rejected: verb {verb!r} not in allowlist {sorted(_ALLOWED_VERBS)!r}")

    def _resolve_cwd(self, cwd: str) -> Path:
        path = Path(cwd).expanduser().resolve()
        # Must be a subdir of base_cwd or /tmp. Keeps the agent inside
        # its sandbox even if it dreams up `/etc` or similar.
        for allowed_root in (self._base.resolve(), Path("/tmp").resolve(), Path("/var/lib/briar").resolve()):
            try:
                path.relative_to(allowed_root)
                return path
            except ValueError:
                continue
        raise ToolError(f"bash-tool rejected: cwd {path} outside allowed roots {self._base}/, /tmp/, /var/lib/briar/")


class ReadFileTool:
    name = "read_file"
    description = "Read the contents of a file. Useful for inspecting source files before editing."

    INPUT_SCHEMA: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path to the file"},
        },
        "required": ["path"],
    }

    def __init__(self, allowed_roots: List[Path]) -> None:
        self._roots = [r.resolve() for r in allowed_roots]

    def run(self, path: str) -> str:
        p = self._validate(path)
        log.debug("read_file-tool: path=%s", p)
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolError(f"read_file: {exc}") from exc

    def _validate(self, path: str) -> Path:
        p = Path(path).expanduser().resolve()
        for root in self._roots:
            try:
                p.relative_to(root)
                return p
            except ValueError:
                continue
        raise ToolError(f"read_file rejected: {p} outside allowed roots {self._roots}")


class WriteFileTool:
    name = "write_file"
    description = "Overwrite a file with new contents. Caller is responsible for preserving content they want to keep."

    INPUT_SCHEMA: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path"},
            "content": {"type": "string", "description": "Full file content"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, allowed_roots: List[Path]) -> None:
        self._roots = [r.resolve() for r in allowed_roots]

    def run(self, path: str, content: str) -> str:
        p = self._validate(path)
        log.info("write_file-tool: path=%s bytes=%d", p, len(content))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} bytes to {p}"

    def _validate(self, path: str) -> Path:
        p = Path(path).expanduser().resolve()
        for root in self._roots:
            try:
                p.relative_to(root)
                return p
            except ValueError:
                continue
        raise ToolError(f"write_file rejected: {p} outside allowed roots {self._roots}")


class EditFileTool:
    name = "edit_file"
    description = "Replace one occurrence of old_text with new_text inside a file. The old_text must match exactly once."

    INPUT_SCHEMA: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    def __init__(self, allowed_roots: List[Path]) -> None:
        self._roots = [r.resolve() for r in allowed_roots]

    def run(self, path: str, old_text: str, new_text: str) -> str:
        p = self._validate(path)
        if not p.exists():
            raise ToolError(f"edit_file: {p} does not exist")
        content = p.read_text(encoding="utf-8")
        occurrences = content.count(old_text)
        if occurrences == 0:
            raise ToolError(f"edit_file: old_text not found in {p}")
        if occurrences > 1:
            raise ToolError(f"edit_file: old_text matches {occurrences} times in {p} — make it more specific")
        p.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        log.info("edit_file-tool: path=%s delta=%+d bytes", p, len(new_text) - len(old_text))
        return f"replaced 1 occurrence in {p} ({len(new_text) - len(old_text):+d} bytes)"

    def _validate(self, path: str) -> Path:
        p = Path(path).expanduser().resolve()
        for root in self._roots:
            try:
                p.relative_to(root)
                return p
            except ValueError:
                continue
        raise ToolError(f"edit_file rejected: {p} outside allowed roots {self._roots}")
