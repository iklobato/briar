"""A GitLab with state and no network, for `briar serve` end to end on `code_host: gitlab`
(test plan F-GL). It answers the `GitlabRest` calls of `GitlabHost` like the v4 REST API:
merge requests (create, read, retitle, list), notes, discussions, the head pipeline of an
MR (plain or merged results), commits, branches, members and the token. Branch heads come
from the local bare origin, so the pushes of the routine are what the fake sees.

Docs modelled: https://docs.gitlab.com/api/merge_requests/, https://docs.gitlab.com/api/notes/,
https://docs.gitlab.com/ci/pipelines/merged_results_pipelines/."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote

from briar.serve.git import Shell

Handler = Callable[[re.Match[str], Dict[str, Any]], Any]
DEVELOPER = 30
OWNER = 50
USER_ID = 1


@dataclass(frozen=True)
class PipelinePlan:
    """The status of the pipeline of each pushed head, in push order (the last one repeats),
    and whether CI runs on a merge commit of the head and the target (merged results)."""

    statuses: List[str] = field(default_factory=lambda: ["success"])
    merged_results: bool = False


@dataclass(frozen=True)
class GitlabServer:
    """The `GitlabRest` surface over in-memory merge requests and a local bare origin."""

    origin: Path
    pipelines: PipelinePlan = field(default_factory=PipelinePlan)
    merge_requests: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    notes: Dict[int, List[str]] = field(default_factory=dict)
    heads: List[str] = field(default_factory=list)
    calls: List[Tuple[str, str]] = field(default_factory=list)

    def get(self, path: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        found: Dict[str, Any] = self._route("GET", path, query or {})
        return found

    def pages(self, path: str, query: Optional[Dict[str, Any]] = None, cap: int = 0) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = self._route("LIST", path, query or {})
        return found[:cap] if cap else found

    def text(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        return "job log: the test failed"

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        found: Dict[str, Any] = self._route("POST", path, body)
        return found

    def put(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        found: Dict[str, Any] = self._route("PUT", path, body)
        return found

    def _route(self, verb: str, path: str, query_or_body: Dict[str, Any]) -> Any:
        self.calls.append((verb, path))
        for method, pattern, handler in Routes(self).table():
            matched = re.fullmatch(pattern, unquote(path))
            if method == verb and matched:
                return handler(matched, query_or_body)
        raise AssertionError(f"the GitLab fake has no route for {verb} {path}")


class Routes:
    """Method, path pattern (decoded) and handler of each GitLab call `serve` makes."""

    PROJECT = r"/projects/(?P<repo>[^/]+/[^/]+)"
    MR = PROJECT + r"/merge_requests/(?P<iid>\d+)"

    def __init__(self, server: GitlabServer) -> None:
        self._server = server

    def table(self) -> List[Tuple[str, str, Handler]]:
        mrs = MergeRequests(self._server)
        repo = RepoReads(self._server)
        return [
            ("GET", r"/user", repo.user),
            ("GET", r"/personal_access_tokens/self", repo.token),
            ("GET", self.MR, mrs.one),
            ("PUT", self.MR, mrs.retitle),
            ("POST", self.MR + r"/notes", mrs.note),
            ("LIST", self.MR + r"/discussions", mrs.discussions),
            ("LIST", self.MR + r"/pipelines", mrs.pipelines),
            ("LIST", self.PROJECT + r"/pipelines/\d+/jobs", mrs.failed_jobs),
            ("POST", self.PROJECT + r"/merge_requests", mrs.create),
            ("LIST", self.PROJECT + r"/merge_requests", mrs.listed),
            ("GET", self.PROJECT + r"/repository/branches/(?P<branch>.+)", repo.branch),
            ("GET", self.PROJECT + r"/repository/commits/(?P<sha>.+)", repo.commit),
            ("GET", self.PROJECT + r"/repository/files/.+", repo.file),
            ("GET", self.PROJECT + r"/members/all/\d+", repo.member),
            ("GET", self.PROJECT, repo.project),
        ]


class MergeRequests:
    """The merge requests of the fake, with `draft` taken from the title as GitLab does."""

    DRAFT = re.compile(r"^\s*(draft:|\[draft\]|\(draft\))", re.IGNORECASE)

    def __init__(self, server: GitlabServer) -> None:
        self._server = server

    def create(self, matched: re.Match[str], body: Dict[str, Any]) -> Dict[str, Any]:
        iid = len(self._server.merge_requests) + 1
        title = str(body.get("title") or "")
        draft = bool(body.get("draft")) or bool(self.DRAFT.match(title))
        self._server.merge_requests[iid] = {**body, "iid": iid, "state": "opened", "title": ("Draft: " + title) if draft and not self.DRAFT.match(title) else title}
        return {"iid": iid}

    def one(self, matched: re.Match[str], query: Dict[str, Any]) -> Dict[str, Any]:
        return self.view(int(matched["iid"]))

    def view(self, iid: int) -> Dict[str, Any]:
        found = dict(self._server.merge_requests[iid])
        head = RepoReads(self._server).head(str(found["source_branch"]))
        return {**found, "draft": bool(self.DRAFT.match(str(found["title"]))), "sha": head, "head_pipeline": Pipelines(self._server).of(iid, head)}

    def retitle(self, matched: re.Match[str], body: Dict[str, Any]) -> Dict[str, Any]:
        self._server.merge_requests[int(matched["iid"])]["title"] = str(body["title"])
        return self.one(matched, {})

    def note(self, matched: re.Match[str], body: Dict[str, Any]) -> Dict[str, Any]:
        self._server.notes.setdefault(int(matched["iid"]), []).append(str(body["body"]))
        return {"id": len(self._server.notes[int(matched["iid"])])}

    def discussions(self, matched: re.Match[str], query: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    def pipelines(self, matched: re.Match[str], query: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [{"id": len(self._server.heads)}]

    def failed_jobs(self, matched: re.Match[str], query: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [{"id": 1, "name": "tests", "stage": "test", "web_url": "https://gitlab.example.test/jobs/1"}]

    def listed(self, matched: re.Match[str], query: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = [self.view(iid) for iid in self._server.merge_requests]
        branch, state = query.get("source_branch"), query.get("state", "all")
        return [row for row in reversed(rows) if (not branch or row["source_branch"] == branch) and state in ("all", row["state"])]


class Pipelines:
    """The head pipeline of an MR: one per pushed head, its status from the plan."""

    def __init__(self, server: GitlabServer) -> None:
        self._server = server

    def of(self, iid: int, head: str) -> Dict[str, Any]:
        if head not in self._server.heads:
            self._server.heads.append(head)
        plan = self._server.pipelines
        status = plan.statuses[min(self._server.heads.index(head), len(plan.statuses) - 1)]
        if plan.merged_results:
            return {"status": status, "sha": f"merge-of-{head}", "ref": f"refs/merge-requests/{iid}/merge"}
        return {"status": status, "sha": head, "ref": f"refs/heads/{head}"}


class RepoReads:
    """Project, branch, commit, file, member and token reads."""

    def __init__(self, server: GitlabServer) -> None:
        self._server = server

    def head(self, branch: str) -> str:
        done = Shell().line(f"git rev-parse refs/heads/{branch}", self._server.origin)
        return done.output.strip() if done.ok() else ""

    def user(self, matched: re.Match[str], query: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": USER_ID, "username": "briar-bot", "commit_email": "briar-bot@example.test"}

    def token(self, matched: re.Match[str], query: Dict[str, Any]) -> Dict[str, Any]:
        return {"scopes": ["api"]}

    def project(self, matched: re.Match[str], query: Dict[str, Any]) -> Dict[str, Any]:
        return {"default_branch": "main", "ci_config_path": "", "permissions": {"project_access": {"access_level": OWNER}}}

    def branch(self, matched: re.Match[str], query: Dict[str, Any]) -> Dict[str, Any]:
        return {"commit": {"id": self.head(matched["branch"])}}

    def commit(self, matched: re.Match[str], query: Dict[str, Any]) -> Dict[str, Any]:
        sha = matched["sha"]
        head = sha.removeprefix("merge-of-")
        return {"id": sha, "parent_ids": [self.head("main"), head] if head != sha else [self.head("main")]}

    def file(self, matched: re.Match[str], query: Dict[str, Any]) -> Dict[str, Any]:
        return {"file_name": ".gitlab-ci.yml"}

    def member(self, matched: re.Match[str], query: Dict[str, Any]) -> Dict[str, Any]:
        return {"access_level": DEVELOPER}
