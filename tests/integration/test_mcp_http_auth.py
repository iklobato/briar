"""End-to-end: the MCP server's HTTP transport + bearer auth.

Spawns ``briar mcp serve --transport http`` with a token and asserts that an
unauthenticated request is rejected (401) while an authenticated one gets past
the auth layer. Proves the wiring; the MCP protocol handshake itself is covered
by the stdio test.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import httpx
import pytest

pytest.importorskip("mcp", reason="requires the `mcp` extra: pip install 'briar-cli[mcp]'")

pytestmark = pytest.mark.integration

_TOKEN = "test-secret-token"
_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_server(tmp_path):
    port = _free_port()
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "HOME": __import__("os").environ.get("HOME", ""),
        "PYTHONPATH": __import__("os").environ.get("PYTHONPATH", ""),
        "BRIAR_TEST_MCP_TOKEN": _TOKEN,
        "BRIAR_TELEMETRY": "off",
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "briar",
            "mcp",
            "serve",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--token-env",
            "BRIAR_TEST_MCP_TOKEN",
            "--root",
            str(tmp_path / "kn"),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        # Wait for the listener.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("mcp http server exited during startup")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("mcp http server did not start in time")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _initialize_body() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
    }


def test_unauthenticated_request_is_401(http_server) -> None:
    resp = httpx.post(http_server, json=_initialize_body(), headers=_MCP_HEADERS, timeout=10)
    assert resp.status_code == 401


def test_wrong_token_is_401(http_server) -> None:
    headers = {**_MCP_HEADERS, "Authorization": "Bearer wrong"}
    resp = httpx.post(http_server, json=_initialize_body(), headers=headers, timeout=10)
    assert resp.status_code == 401


def test_authenticated_request_passes_auth(http_server) -> None:
    headers = {**_MCP_HEADERS, "Authorization": f"Bearer {_TOKEN}"}
    resp = httpx.post(http_server, json=_initialize_body(), headers=headers, timeout=10)
    # Auth passed → the request reached the MCP layer (a successful initialize
    # is 200; anything other than 401 proves the bearer check let it through).
    assert resp.status_code != 401
