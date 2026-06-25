"""HTTP bind policy + bearer-auth middleware for the MCP server."""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from briar.errors import CliError
from briar.mcpserver._auth import BearerAuthMiddleware, check_bind_policy

# ── bind policy ─────────────────────────────────────────────────────────


def test_loopback_without_token_is_allowed() -> None:
    check_bind_policy("127.0.0.1", "")  # no raise


def test_public_host_without_token_is_refused() -> None:
    with pytest.raises(CliError, match="non-loopback host"):
        check_bind_policy("0.0.0.0", "")


def test_public_host_with_token_is_allowed() -> None:
    check_bind_policy("0.0.0.0", "secret")  # no raise


# ── bearer middleware ───────────────────────────────────────────────────


class _Recorder:
    """Captures ASGI send events and whether the inner app was reached."""

    def __init__(self) -> None:
        self.events: List[dict] = []
        self.inner_called = False

    async def inner_app(self, scope, receive, send) -> None:
        self.inner_called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(self, event: dict) -> None:
        self.events.append(event)

    @staticmethod
    async def receive() -> dict:
        return {"type": "http.request"}


def _http_scope(auth: bytes | None):
    headers = [(b"authorization", auth)] if auth is not None else []
    return {"type": "http", "headers": headers}


def _drive(mw, scope, rec) -> None:
    asyncio.run(mw(scope, rec.receive, rec.send))


def test_correct_token_passes_through() -> None:
    rec = _Recorder()
    mw = BearerAuthMiddleware(rec.inner_app, "s3cret")
    _drive(mw, _http_scope(b"Bearer s3cret"), rec)
    assert rec.inner_called is True
    assert rec.events[0]["status"] == 200


def test_missing_token_is_401_and_blocks_app() -> None:
    rec = _Recorder()
    mw = BearerAuthMiddleware(rec.inner_app, "s3cret")
    _drive(mw, _http_scope(None), rec)
    assert rec.inner_called is False
    assert rec.events[0]["status"] == 401


def test_wrong_token_is_401() -> None:
    rec = _Recorder()
    mw = BearerAuthMiddleware(rec.inner_app, "s3cret")
    _drive(mw, _http_scope(b"Bearer nope"), rec)
    assert rec.inner_called is False
    assert rec.events[0]["status"] == 401


def test_non_http_scope_passes_through() -> None:
    rec = _Recorder()
    mw = BearerAuthMiddleware(rec.inner_app, "s3cret")
    _drive(mw, {"type": "lifespan"}, rec)
    assert rec.inner_called is True
