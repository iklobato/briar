"""MCP client manager — a sync bridge over the async `mcp` SDK.

The agent runner loop is synchronous; the official `mcp` SDK is async
(stdio subprocess + Streamable-HTTP sessions that must stay open across
calls). This manager owns a single background event-loop thread that
holds every `ClientSession` and exposes a *synchronous* surface:

  * `start()`  — spin up the loop thread, connect every enabled server,
    return the wrapped `McpTool`s (one per advertised tool, post-allowlist).
  * `call()`   — invoke one tool on one server, blocking the caller.
  * `close()`  — tear every session down and stop the loop thread.

The anyio task groups inside `stdio_client` / `ClientSession` must be
entered AND exited in the same task, so all session contexts live inside
one long-lived supervisor coroutine (`_supervise`). It opens everything,
hands the listed tools back to `start()` via a thread-safe future, waits
on a shutdown event, then unwinds the stack in that same task. Tool calls
are dispatched as separate coroutines that merely *use* the already-open
session objects (safe across tasks) — they never touch the context
managers.

Secrets never appear in the runbook: `env` / `headers` values are env-var
NAMES, resolved here from briar's own environment (same indirection as
`KnowledgeBinding.dsn_env`).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import threading
from typing import Any, Dict, List, Mapping, Optional, Tuple

from briar.mcp._errors import McpError
from briar.mcp._tool import McpTool

log = logging.getLogger(__name__)


# (handle, tool_name, description, input_schema)
_ToolMeta = Tuple[str, str, str, Dict[str, Any]]


class McpClientManager:
    """Owns the MCP event-loop thread and every open session.

    One instance per agent run. Not thread-safe for concurrent `call()`s
    — the runner drives one tool at a time, which matches the loop."""

    def __init__(
        self,
        bindings: Mapping[str, Any],
        *,
        connect_timeout_s: float = 30.0,
        call_timeout_s: float = 60.0,
        close_timeout_s: float = 15.0,
    ) -> None:
        self._bindings = dict(bindings)
        self._connect_timeout_s = connect_timeout_s
        self._call_timeout_s = call_timeout_s
        self._close_timeout_s = close_timeout_s

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._supervisor: Optional[concurrent.futures.Future] = None
        self._ready: concurrent.futures.Future = concurrent.futures.Future()
        self._shutdown: Optional[asyncio.Event] = None
        self._sessions: Dict[str, Any] = {}

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> List[McpTool]:
        """Connect every enabled server and return the bound tools.

        Per-server connect / list failures are logged and that server is
        skipped — one broken server does not sink the others. Raises
        `McpError` only for hard failures (SDK missing, supervisor never
        signalled ready within the connect timeout)."""
        self._require_sdk()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(self._loop,),
            name="briar-mcp",
            daemon=True,
        )
        self._thread.start()
        self._supervisor = asyncio.run_coroutine_threadsafe(self._supervise(), self._loop)
        try:
            tool_meta: List[_ToolMeta] = self._ready.result(timeout=self._connect_timeout_s)
        except concurrent.futures.TimeoutError as exc:
            self.close()
            raise McpError(f"MCP servers did not connect within {self._connect_timeout_s:.0f}s") from exc
        except Exception as exc:  # noqa: BLE001 — surfaced with context below
            self.close()
            raise McpError(f"MCP manager failed to start: {exc}") from exc

        return [McpTool(self, handle, name, desc, schema, purpose=self._purpose_for(handle)) for (handle, name, desc, schema) in tool_meta]

    def close(self) -> None:
        """Idempotent teardown. Signals the supervisor to unwind its
        session stack, then stops and joins the loop thread."""
        loop = self._loop
        if loop is None:
            return
        if self._shutdown is not None:
            loop.call_soon_threadsafe(self._shutdown.set)
        if self._supervisor is not None:
            try:
                self._supervisor.result(timeout=self._close_timeout_s)
            except Exception:  # noqa: BLE001 — teardown is best-effort
                log.warning("mcp: supervisor did not shut down cleanly", exc_info=True)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=self._close_timeout_s)
        if not loop.is_running():
            loop.close()
        self._loop = None
        self._thread = None
        self._supervisor = None
        self._sessions = {}

    # -- sync call surface -------------------------------------------------

    def call(self, handle: str, tool_name: str, arguments: Optional[Dict[str, Any]]) -> Any:
        """Invoke one tool, blocking until it returns. Returns the raw
        `CallToolResult` — the `McpTool` stringifies it. Raises `McpError`
        on transport/timeout failure."""
        loop = self._loop
        if loop is None:
            raise McpError("mcp manager is not running")
        coro = self._call_async(handle, tool_name, arguments or {})
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=self._call_timeout_s + 5.0)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise McpError(f"mcp tool {handle}/{tool_name} timed out after {self._call_timeout_s:.0f}s") from exc

    # -- internals (run on the loop thread) --------------------------------

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    async def _supervise(self) -> None:
        """Open every session inside one task, publish the tool list, wait
        for shutdown, then unwind — all in this single task so anyio's
        cancel scopes are entered and exited in the same place."""
        from contextlib import AsyncExitStack

        self._shutdown = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                tool_meta: List[_ToolMeta] = []
                for handle, binding in self._bindings.items():
                    if not getattr(binding, "enabled", True):
                        continue
                    meta = await self._connect_one(stack, handle, binding)
                    tool_meta.extend(meta)
                if not self._ready.done():
                    self._ready.set_result(tool_meta)
                await self._shutdown.wait()
        except Exception as exc:  # noqa: BLE001 — propagate to start() via the future
            if not self._ready.done():
                self._ready.set_exception(exc)
            else:
                log.exception("mcp: supervisor crashed after startup")

    async def _connect_one(self, stack: Any, handle: str, binding: Any) -> List[_ToolMeta]:
        """Open one session and list its tools. Failures are isolated to
        this server: logged and returned as an empty list."""
        try:
            session = await self._open_session(stack, binding)
        except Exception:  # noqa: BLE001 — one server's failure must not sink the rest
            log.exception("mcp: failed to connect server=%s — skipping", handle)
            return []
        self._sessions[handle] = session
        try:
            listed = await session.list_tools()
        except Exception:  # noqa: BLE001
            log.exception("mcp: list_tools failed server=%s — skipping", handle)
            return []

        allow = set(getattr(binding, "tools", []) or [])
        out: List[_ToolMeta] = []
        for tool in listed.tools:
            if allow and tool.name not in allow:
                continue
            schema = getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}
            out.append((handle, tool.name, tool.description or "", schema))
        log.info("mcp: server=%s connected, bound %d tool(s)", handle, len(out))
        return out

    async def _open_session(self, stack: Any, binding: Any) -> Any:
        from mcp import ClientSession

        if binding.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            resolved = self._resolve_env(binding.env)
            env: Optional[Dict[str, str]]
            if resolved:
                # The SDK replaces the whole subprocess environment when
                # `env` is set, so merge over the default (keeps PATH etc.
                # — without it `docker` / `npx` fail to launch).
                from mcp.client.stdio import get_default_environment

                env = {**get_default_environment(), **resolved}
            else:
                env = None
            params = StdioServerParameters(command=binding.command, args=list(binding.args), env=env)
            read, write = await stack.enter_async_context(stdio_client(params))
        else:
            from mcp.client.streamable_http import streamablehttp_client

            headers = self._resolve_env(binding.headers)
            transport = await stack.enter_async_context(streamablehttp_client(binding.url, headers=headers or None))
            read, write = transport[0], transport[1]

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def _call_async(self, handle: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        session = self._sessions.get(handle)
        if session is None:
            raise McpError(f"mcp server {handle!r} is not connected")
        return await asyncio.wait_for(session.call_tool(tool_name, arguments), timeout=self._call_timeout_s)

    # -- helpers -----------------------------------------------------------

    def _purpose_for(self, handle: str) -> str:
        """The server's optional `purpose:` string — folded into each of
        its tools' descriptions so the model can judge when to use it."""
        binding = self._bindings.get(handle)
        return (getattr(binding, "purpose", "") or "") if binding is not None else ""

    @staticmethod
    def _resolve_env(mapping: Optional[Mapping[str, str]]) -> Dict[str, str]:
        """Resolve {key: ENV_VAR_NAME} → {key: value} from os.environ.
        An unset env var is dropped with a warning, never defaulted to a
        literal — a half-configured server should fail visibly, not run
        with a blank credential silently substituted."""
        out: Dict[str, str] = {}
        for key, env_name in (mapping or {}).items():
            value = os.environ.get(env_name)
            if value is None:
                log.warning("mcp: env var %r (for %r) is unset — omitting", env_name, key)
                continue
            out[key] = value
        return out

    @staticmethod
    def _require_sdk() -> None:
        try:
            import mcp  # noqa: F401
        except ImportError as exc:
            raise McpError("MCP support requires the `mcp` package. Install it with: " "pip install 'briar-cli[mcp]'") from exc
