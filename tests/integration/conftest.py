"""Integration-tier fixtures: run the REAL command + REAL SDK client against a
wire-level fake, so parsing, serialization, pagination, and output rendering all
execute — only the remote server is faked.

A real `http.server` on 127.0.0.1 (not a function patch) so it works for every
transport the code uses: PyGithub's urllib requester and atlassian's `requests`.
AWS goes through `moto` instead (see those tests).
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Tuple

import pytest


# The status of a pattern route (a found resource).
FOUND = 200


class _MockApi:
    def __init__(self) -> None:
        # (method, path_prefix) -> queue of (status, json_body). Calling add()
        # twice for the same route enqueues a SEQUENCE (consumed in order, the
        # last response sticks) so multi-turn flows (tool_use -> end_turn) work.
        # Longest-prefix-first so specific routes beat generic ones.
        # body is the value to return. With raw=False (default) it is JSON-encoded
        # and served as application/json; with raw=True a `str`/`bytes` body is
        # served verbatim as text/plain (for sinks whose API returns a bare
        # non-JSON body, e.g. a Slack incoming webhook's literal "ok").
        self.routes: Dict[Tuple[str, str], List[Tuple[int, Any, bool]]] = {}
        # (method, full-path regex) -> body: answers a whole path, before the prefix
        # routes (the login check reads `/repos/<owner>/<repo>` itself, spec 005 PR 3).
        self.patterns: List[Tuple[str, Any, Any]] = []
        self.received: List[Dict[str, Any]] = []
        self.base_url = ""

    def add_pattern(self, method: str, path_regex: str, body: Any) -> None:
        self.patterns.append((method.upper(), re.compile(path_regex), body))

    def add(self, method: str, path_prefix: str, body: Any, status: int = 200, raw: bool = False) -> None:
        self.routes.setdefault((method.upper(), path_prefix), []).append((status, body, raw))

    def _match(self, method: str, path: str) -> Tuple[int, Any, bool] | None:
        for verb, pattern, body in self.patterns:
            if verb == method and pattern.fullmatch(path):
                return (FOUND, body, False)
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
            status, body, raw = match
            if raw:
                payload = body if isinstance(body, bytes) else str(body).encode()
                content_type = "text/plain"
            else:
                payload = json.dumps(body).encode()
                content_type = "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
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
    """Point telemetry at a fresh, empty per-test config dir so no test reads
    or writes the real home; with nothing saved the tier is the 2.0 default,
    off. Tests that manage their own XDG_CONFIG_HOME override this in their
    body."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))


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
    retry, and pagination — only base_url changes). Saves a placeholder GitHub
    login for company `acme` (spec 005: `BRIAR_ACME_GITHUB_TOKEN`)."""
    from briar.extract import _gh

    monkeypatch.setenv("BRIAR_ACME_GITHUB_TOKEN", "PLACEHOLDER-not-a-secret")

    def client(self: _gh.GithubApi) -> object:
        from github import Auth, Github

        return Github(base_url=mock_api.base_url, auth=Auth.Token(self._require_token()), per_page=100, retry=0, timeout=10)

    monkeypatch.setattr(_gh.GithubApi, "client", client)
    # The login check (spec 005 PR 3): "who am I", then each repo the run reads.
    mock_api.add("GET", "/user", {"login": "octo"})
    mock_api.add_pattern("GET", r"/repos/[^/]+/[^/]+", {"full_name": "checked"})
    return mock_api


@pytest.fixture
def bitbucket_at(mock_api, monkeypatch):
    """Point the REAL atlassian Bitbucket Cloud client at the mock server by
    overriding the writer's BASE. Sets the per-company basic-auth creds."""
    from briar.messaging.bitbucket_pr_comment import BitbucketPrCommentWriter

    monkeypatch.setattr(BitbucketPrCommentWriter, "BASE", mock_api.base_url + "/")
    monkeypatch.setenv("BITBUCKET_ACME_USERNAME", "bot")
    monkeypatch.setenv("BITBUCKET_ACME_APP_PASSWORD", "PLACEHOLDER-not-a-secret")
    monkeypatch.setenv("BITBUCKET_ACME_WORKSPACE", "acme-ws")
    return mock_api


@pytest.fixture
def jira_at(mock_api, monkeypatch):
    """Point the REAL atlassian Jira client at the mock server: its site goes in
    `[auth.jira] site` (G4), so the test writes it with `jira_site_toml`. Saves a
    placeholder Jira login for company `acme`. Caller seeds the REST routes."""
    monkeypatch.setenv("BRIAR_ACME_JIRA_EMAIL", "bot@example.test")
    monkeypatch.setenv("BRIAR_ACME_JIRA_TOKEN", "PLACEHOLDER-not-a-secret")
    mock_api.jira_site_toml = f'[auth.jira]\nsite = "{mock_api.base_url}"\n'
    # The login check (spec 005 PR 3): "who am I", then each project the run reads.
    mock_api.add_pattern("GET", r"/rest/api/[23]/myself", {"emailAddress": "bot@example.test"})
    mock_api.add_pattern("GET", r"/rest/api/[23]/project/[A-Z][A-Z0-9_]*", {"key": "checked"})
    return mock_api


@pytest.fixture
def graphql_at(mock_api: _MockApi, monkeypatch: pytest.MonkeyPatch) -> _MockApi:
    """Point the Linear and Fireflies GraphQL calls at the mock server (`POST /graphql`).
    Caller seeds the answers with `mock_api.add("POST", "/graphql", ...)`."""
    monkeypatch.setattr("briar.extract._trackers.linear._ENDPOINT", f"{mock_api.base_url}/graphql")
    monkeypatch.setattr("briar.extract._meetings.fireflies._ENDPOINT", f"{mock_api.base_url}/graphql")
    return mock_api
