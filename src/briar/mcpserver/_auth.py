"""HTTP transport + auth for the MCP server.

The Streamable-HTTP transport is reachable over the network, so unlike stdio
it needs an access control story:

  * **Loopback by default.** The CLI binds 127.0.0.1 unless told otherwise.
  * **No public bind without a token.** Binding a non-loopback host without a
    bearer token is refused outright — fail closed, never silently expose an
    unauthenticated control surface.
  * **Bearer token.** When a token is configured, every HTTP request must carry
    ``Authorization: Bearer <token>`` (constant-time compared); anything else
    gets 401 before reaching the MCP layer.

The token itself comes from an env var NAME on the CLI (``--token-env``), never
a literal flag value — same env-var indirection as the rest of briar.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Awaitable, Callable

from briar.errors import CliError

log = logging.getLogger(__name__)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_Scope = dict
_Receive = Callable[[], Awaitable[dict]]
_Send = Callable[[dict], Awaitable[None]]


def check_bind_policy(host: str, token: str) -> None:
    """Refuse to bind a non-loopback host without a token. Raises `CliError`."""
    if host not in LOOPBACK_HOSTS and not token:
        raise CliError(
            f"refusing to bind MCP HTTP server to non-loopback host {host!r} without a token — "
            "pass --token-env NAME (an env var holding a bearer token), or bind 127.0.0.1."
        )


class BearerAuthMiddleware:
    """Pure-ASGI middleware: require ``Authorization: Bearer <token>`` on HTTP
    requests, 401 otherwise. Non-HTTP scopes (lifespan) pass straight through."""

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}"

    async def __call__(self, scope: _Scope, receive: _Receive, send: _Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"").decode("latin-1")
        if not hmac.compare_digest(provided, self._expected):
            await self._reject(send)
            return
        await self._app(scope, receive, send)

    @staticmethod
    async def _reject(send: _Send) -> None:
        await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"401 Unauthorized"})


def serve_http(server: Any, *, host: str, port: int, token: str) -> None:
    """Run the MCP server over Streamable HTTP at host:port, wrapped in bearer
    auth when a token is set. Blocks until the server is stopped."""
    check_bind_policy(host, token)
    server.settings.host = host
    server.settings.port = port

    app = server.streamable_http_app()
    if token:
        app = BearerAuthMiddleware(app, token)
        log.info("mcp-serve: HTTP bearer auth enabled on %s:%d", host, port)
    else:
        log.warning("mcp-serve: HTTP on %s:%d with NO auth (loopback only)", host, port)

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
