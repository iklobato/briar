"""End to end: `briar extract` driving the REAL `GitlabProvider` and the REAL python-gitlab
client against a local GitLab fake (spec 008 PR 3a, G2, G17, G18).

The command, the `[auth.gitlab]` login, python-gitlab (its token header, its `Link`
pagination, its wait on a 429), the extractor and the file store all run; only the server
is faked. The project path has three levels (G2).

GitLab REST doc shapes:
- user:           https://docs.gitlab.com/api/users/#get-the-current-user
- project:        https://docs.gitlab.com/api/projects/#get-a-single-project
- merge requests: https://docs.gitlab.com/api/merge_requests/#list-project-merge-requests
- pagination:     https://docs.gitlab.com/api/rest/#pagination-link-header
- rate limit:     https://docs.gitlab.com/security/rate_limits/
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, ClassVar, Dict, Iterator, List, Mapping, Tuple
from urllib.parse import parse_qs, urlsplit

import pytest

from briar.config import load_project_config

pytestmark = pytest.mark.integration

FAKE_TOKEN = "PLACEHOLDER-not-a-secret"
REPO = "acme/platform/app"
PROJECT = "/api/v4/projects/acme%2Fplatform%2Fapp"
OK, TOO_MANY, NOT_FOUND = 200, 429, 404
FIRST, SECOND, THIRD = 1, 2, 3
MERGED = THIRD
JOIN_SECONDS = 5


@dataclass(frozen=True)
class Answer:
    status: int
    body: Any
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FakeGitlab:
    """Answers by (path, page); a route with more than one answer gives them in order and
    keeps the last. `received` records every path with its query and its token header."""

    routes: Dict[Tuple[str, str], List[Answer]] = field(default_factory=dict)
    received: List[Tuple[str, str]] = field(default_factory=list)
    base_url: List[str] = field(default_factory=list)

    def add(self, path: str, page: str, answer: Answer) -> None:
        self.routes.setdefault((path, page), []).append(answer)

    def answer(self, target: str, token: str) -> Answer:
        self.received.append((target, token))
        parts = urlsplit(target)
        page = parse_qs(parts.query).get("page", ["1"])[0]
        queue = self.routes.get((parts.path, page), [])
        if not queue:
            return Answer(NOT_FOUND, {"message": "404 Not Found"})
        return queue.pop(0) if len(queue) > 1 else queue[0]


class GitlabHandler(BaseHTTPRequestHandler):
    fake: ClassVar[FakeGitlab]

    def do_GET(self) -> None:
        answer = self.fake.answer(self.path, self.headers.get("PRIVATE-TOKEN", ""))
        payload = json.dumps(answer.body).encode()
        self.send_response(answer.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in answer.headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)


class MergeRequests:
    """Merged MRs, as the list endpoint shows them, on two pages after one 429."""

    LISTING = f"{PROJECT}/merge_requests"

    def seed(self, fake: FakeGitlab) -> None:
        site = fake.base_url[0]
        fake.add("/api/v4/user", "1", Answer(OK, {"username": "octo"}))
        fake.add(PROJECT, "1", Answer(OK, {"id": 1, "default_branch": "main"}))
        fake.add(self.LISTING, "1", Answer(TOO_MANY, {"message": "429 Too Many Requests"}, {"Retry-After": "0"}))
        next_page = f'<{site}{self.LISTING}?page=2&per_page=100&state=merged>; rel="next"'
        fake.add(self.LISTING, "1", Answer(OK, [self.merged(FIRST, "ana"), self.merged(SECOND, "bo")], {"Link": next_page}))
        fake.add(self.LISTING, "2", Answer(OK, [self.merged(THIRD, "ana")]))

    def merged(self, iid: int, author: str) -> Dict[str, Any]:
        return {
            "iid": iid,
            "title": f"Change {iid}",
            "author": {"username": author},
            "draft": False,
            "source_branch": f"feat-{iid}",
            "target_branch": "main",
            "user_notes_count": 1,
            "created_at": "2026-01-01T09:00:00Z",
            "merged_at": "2026-01-02T10:00:00Z",
            "reviewers": [],
            "assignees": [],
            "description": "",
        }


@pytest.fixture
def gitlab_server() -> Iterator[FakeGitlab]:
    fake = FakeGitlab()
    handler = type("Handler", (GitlabHandler,), {"fake": fake})
    server = HTTPServer(("127.0.0.1", 0), handler)
    fake.base_url.append(f"http://127.0.0.1:{server.server_address[1]}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake
    finally:
        server.shutdown()
        thread.join(timeout=JOIN_SECONDS)
        server.server_close()


@pytest.fixture(autouse=True)
def project_in_tmp_root(cli: Callable[..., SimpleNamespace], tmp_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs after `cli`, which stubs the project config reader: puts the real one back and
    runs in `tmp_root`, where the test writes its `.briar.toml`."""
    monkeypatch.setattr("briar.config.load_project_config", load_project_config)
    monkeypatch.chdir(tmp_root)


@pytest.fixture
def gitlab_company(gitlab_server: FakeGitlab, tmp_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Company acme reads the fake GitLab: `[auth.gitlab] url`, `[extract] provider`, the token."""
    (tmp_root / ".briar.toml").write_text(f'company = "acme"\n\n[auth.gitlab]\nurl = "{gitlab_server.base_url[0]}"\n\n[extract]\nprovider = "gitlab"\n')
    monkeypatch.setenv("BRIAR_ACME_GITLAB_TOKEN", FAKE_TOKEN)


@pytest.mark.usefixtures("gitlab_company")
class TestPrArchaeologyOnGitlab:
    def test_walks_the_pages_after_a_rate_limit(self, cli: Callable[..., SimpleNamespace], gitlab_server: FakeGitlab, tmp_root: Path) -> None:
        MergeRequests().seed(gitlab_server)

        run = cli("extract", "--company", "acme", "--include", "pr-archaeology", "--repo", REPO)

        assert run.code == 0, run.err
        listed = [target for target, _ in gitlab_server.received if target.startswith(MergeRequests.LISTING)]
        assert [parse_qs(urlsplit(target).query).get("page", ["1"])[0] for target in listed] == ["1", "1", "2"]
        assert {token for _, token in gitlab_server.received} == {FAKE_TOKEN}
        blob = "\n".join(path.read_text() for path in (tmp_root / "knowledge").rglob("*") if path.is_file())
        assert f"merged PR sample: **{MERGED}**" in blob
