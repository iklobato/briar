"""Integration-tier fixtures: run the REAL command + REAL SDK client against a
wire-level fake, so parsing, serialization, pagination, and output rendering all
execute — only the remote server is faked.

A real `http.server` on 127.0.0.1 (not a function patch) so it works for every
transport the code uses: PyGithub's urllib requester, atlassian's `requests`,
and the Anthropic SDK's httpx. AWS goes through `moto` instead (see those tests).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Tuple

import pytest


class _MockApi:
    def __init__(self) -> None:
        # (method, path_prefix) -> queue of (status, json_body). Calling add()
        # twice for the same route enqueues a SEQUENCE (consumed in order, the
        # last response sticks) so multi-turn flows (tool_use -> end_turn) work.
        # Longest-prefix-first so specific routes beat generic ones.
        self.routes: Dict[Tuple[str, str], List[Tuple[int, Any]]] = {}
        self.received: List[Dict[str, Any]] = []
        self.base_url = ""

    def add(self, method: str, path_prefix: str, body: Any, status: int = 200) -> None:
        self.routes.setdefault((method.upper(), path_prefix), []).append((status, body))

    def _match(self, method: str, path: str) -> Tuple[int, Any] | None:
        candidates = [(p, q) for (m, p), q in self.routes.items() if m == method and path.startswith(p) and q]
        if not candidates:
            return None
        candidates.sort(key=lambda kv: len(kv[0]), reverse=True)
        queue = candidates[0][1]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def _handler_for(api: _MockApi):
    class Handler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            api.received.append({"method": self.command, "path": self.path, "body": raw, "headers": dict(self.headers)})
            match = api._match(self.command, self.path.split("?")[0]) or api._match(self.command, self.path)
            if match is None:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"message":"no route"}')
                return
            status, body = match
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _respond
        do_POST = _respond
        do_PATCH = _respond
        do_PUT = _respond
        do_DELETE = _respond

        def log_message(self, *args: Any) -> None:  # silence the server
            return

    return Handler


@pytest.fixture(autouse=True)
def _quiet_telemetry(tmp_path_factory, monkeypatch):
    """Point telemetry at a fresh per-test config dir with the consent banner
    already marked shown and the tier off, so the CLI's one-time first-run
    notice never lands on stderr (it would otherwise pollute stderr assertions
    on whichever test ran first — flaky under randomized ordering). Tests that
    manage their own XDG_CONFIG_HOME override this in their body."""
    cfg = tmp_path_factory.mktemp("xdg")
    (cfg / "briar").mkdir()
    (cfg / "briar" / "telemetry.json").write_text(json.dumps({"tier": "off", "banner_shown": True}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))


@pytest.fixture
def mock_api():
    api = _MockApi()
    server = HTTPServer(("127.0.0.1", 0), _handler_for(api))
    api.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield api
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()  # close the listening socket (else ResourceWarning -> error)


@pytest.fixture
def github_at(mock_api, monkeypatch):
    """Point the REAL PyGithub client at the mock server (keeps its requester,
    retry, and pagination — only base_url changes). Sets a placeholder token."""
    from briar.extract import _gh

    monkeypatch.setenv("GITHUB_TOKEN", "PLACEHOLDER-not-a-secret")

    def client(cls, token: str = ""):
        from github import Auth, Github

        tok = cls._require_token(token)
        return Github(base_url=mock_api.base_url, auth=Auth.Token(tok), per_page=100, retry=0, timeout=10)

    monkeypatch.setattr(_gh.GithubApi, "client", classmethod(client))
    return mock_api


@pytest.fixture
def anthropic_at(mock_api, monkeypatch):
    """Point the REAL Anthropic SDK at the mock server via the base-url env it
    honors natively. Caller seeds POST /v1/messages with a Messages-API body."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "PLACEHOLDER-not-a-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", mock_api.base_url)
    return mock_api


@pytest.fixture
def jira_at(mock_api, monkeypatch):
    """Point the REAL atlassian Jira client at the mock server (its URL comes
    from JIRA_<CO>_URL). Caller seeds the REST routes."""
    monkeypatch.setenv("JIRA_ACME_URL", mock_api.base_url)
    monkeypatch.setenv("JIRA_ACME_EMAIL", "bot@example.test")
    monkeypatch.setenv("JIRA_ACME_TOKEN", "PLACEHOLDER-not-a-secret")
    return mock_api
