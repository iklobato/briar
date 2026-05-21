"""GitHub `RepositoryProvider` — wraps the existing `GithubApi` facade.

Thin adapter: each verb dispatches to one or two `GithubApi` calls
and normalises the response into the dataclasses defined in
`_provider.py`. All HTTP / retry / rate-limit / pagination handling
stays inside `GithubApi` (PyGithub-backed) — this file is pure
translation.

Auth: `$GITHUB_TOKEN`, workspace-wide. The `company` constructor
parameter is accepted for the abstract contract but ignored — GitHub
PATs span every org the token has access to, so there's no per-tenant
env-var lookup. (Compare with `BitbucketProvider`, where `company`
drives `CredEnv.BITBUCKET_<COMPANY>_*`.)"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List

from briar.decorators import swallow_errors
from briar.extract._gh import GithubApi
from briar.extract._provider import (
    CiFailure,
    CiRun,
    Commit,
    Deployment,
    Environment,
    PullRequest,
    RepositoryProvider,
    ReviewComment,
)


log = logging.getLogger(__name__)


class GithubProvider(RepositoryProvider):
    kind = "github"

    def __init__(self, *, company: str = "") -> None:
        # `company` is part of the abstract signature but inert here —
        # GitHub credentials are workspace-wide.
        self._company = company

    def is_available(self) -> bool:
        return bool(GithubApi.auth_token())

    def list_pulls(self, repo: str, *, state: str, max_count: int) -> List[PullRequest]:
        # Contract: state is "open" | "merged". GitHub vocabulary is
        # "open" | "closed", and merged PRs are a subset of closed
        # where `merged_at` is not null. Translate at the boundary.
        gh_state = "closed" if state == "merged" else state
        pages_needed = max(1, (max_count // 100) + 1)
        path = f"/repos/{repo}/pulls?state={gh_state}&sort=updated&direction=desc"
        rows = GithubApi.get_paginated(path, max_pages=pages_needed)
        if state == "merged":
            rows = [r for r in rows if r.get("merged_at") is not None]
        return [self._to_pull(r) for r in rows[:max_count]]

    def list_environments(self, repo: str) -> List[Environment]:
        envelope = GithubApi.get_json(f"/repos/{repo}/environments")
        envs = envelope.get("environments", []) if type(envelope) is dict else []
        out: List[Environment] = []
        for e in envs:
            out.append(
                Environment(
                    name=e.get("name") or "",
                    protection_rule_count=len(e.get("protection_rules") or []),
                    url=e.get("html_url") or "",
                )
            )
        return out

    def list_deployments(self, repo: str, *, limit: int) -> List[Deployment]:
        rows = GithubApi.get_paginated(
            f"/repos/{repo}/deployments",
            max_pages=1,
            per_page=min(limit, 100),
        )
        out: List[Deployment] = []
        for d in rows[:limit]:
            out.append(
                Deployment(
                    id=str(d.get("id") or ""),
                    environment=d.get("environment") or "",
                    sha=(d.get("sha") or "")[:7],
                    creator=(d.get("creator") or {}).get("login") or "",
                    created_at=d.get("created_at") or "",
                )
            )
        return out

    def list_ci_runs(self, repo: str, *, limit: int) -> List[CiRun]:
        envelope = GithubApi.get_json(f"/repos/{repo}/actions/runs?per_page={limit}")
        runs = envelope.get("workflow_runs", []) if type(envelope) is dict else []
        out: List[CiRun] = []
        for r in runs[:limit]:
            out.append(
                CiRun(
                    name=r.get("name") or "",
                    status=r.get("status") or "",
                    conclusion=r.get("conclusion") or "",
                    head_branch=r.get("head_branch") or "",
                    created_at=r.get("created_at") or "",
                )
            )
        return out

    def read_file(self, repo: str, path: str) -> str:
        try:
            resp = GithubApi.get_json(f"/repos/{repo}/contents/{path}")
        except Exception:  # noqa: BLE001 — 404 is the common case
            return ""
        if type(resp) is not dict or resp.get("type") != "file":
            return ""
        raw = resp.get("content") or ""
        encoding = resp.get("encoding") or "base64"
        if encoding != "base64":
            return raw
        try:
            return base64.b64decode(raw).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""

    @swallow_errors(default=None, message="github get_pull")
    def get_pull(self, repo: str, number: int) -> PullRequest:
        data = GithubApi.get_json(f"/repos/{repo}/pulls/{number}")
        if not isinstance(data, dict):
            return super().get_pull(repo, number)
        return self._to_pull(data)

    @swallow_errors(default=[], message="github list_pr_comments")
    def list_pr_comments(self, repo: str, number: int) -> List[ReviewComment]:
        """Returns inline review comments AND top-level issue comments
        for one PR. The two GitHub endpoints — `/pulls/{n}/comments`
        for review threads, `/issues/{n}/comments` for top-level —
        return different shapes; this method merges them into one
        ReviewComment list."""
        inline = GithubApi.get_paginated(f"/repos/{repo}/pulls/{number}/comments", max_pages=2) or []
        top_level = GithubApi.get_paginated(f"/repos/{repo}/issues/{number}/comments", max_pages=2) or []
        out: List[ReviewComment] = []
        for c in inline:
            out.append(
                ReviewComment(
                    id=str(c.get("id") or ""),
                    author=(c.get("user") or {}).get("login") or "",
                    body=(c.get("body") or "")[:1500],
                    file_path=c.get("path") or "",
                    line=int(c.get("line") or c.get("original_line") or 0),
                    is_resolved=False,  # GitHub doesn't expose this on the REST endpoint
                    created_at=c.get("created_at") or "",
                )
            )
        for c in top_level:
            out.append(
                ReviewComment(
                    id=str(c.get("id") or ""),
                    author=(c.get("user") or {}).get("login") or "",
                    body=(c.get("body") or "")[:1500],
                    file_path="",
                    line=0,
                    is_resolved=False,
                    created_at=c.get("created_at") or "",
                )
            )
        return out

    @swallow_errors(default=[], message="github list_ci_failures")
    def list_ci_failures(self, repo: str, number: int) -> List[CiFailure]:
        """For the PR's head SHA, find failing check-runs and pull a
        short log tail per failure. GitHub's `/check-runs` endpoint
        gives status; the log requires a second call against
        `/actions/jobs/{job_id}/logs` (text, not JSON)."""
        pr = GithubApi.get_json(f"/repos/{repo}/pulls/{number}")
        head_sha = (pr.get("head") or {}).get("sha") if isinstance(pr, dict) else ""
        if not head_sha:
            return []
        envelope = GithubApi.get_json(f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=50")
        runs = envelope.get("check_runs", []) if isinstance(envelope, dict) else []
        out: List[CiFailure] = []
        for run in runs:
            conclusion = (run.get("conclusion") or "").lower()
            if conclusion not in ("failure", "timed_out", "cancelled"):
                continue
            # Find the failing step inside the check-run's output.
            step_name = ""
            for step in (run.get("output") or {}).get("annotations_url") and [] or []:
                step_name = step.get("title") or ""
                break
            out.append(
                CiFailure(
                    workflow=str(run.get("name") or ""),
                    job=str(run.get("name") or ""),
                    step=step_name or "(unknown step)",
                    log_tail=self._tail_check_run_log(repo, run.get("id")),
                    url=str(run.get("html_url") or ""),
                )
            )
        return out

    @staticmethod
    def _tail_check_run_log(repo: str, run_id: Any) -> str:
        if not run_id:
            return ""
        gh = GithubApi.client()
        try:
            headers, body = gh._Github__requester.requestJsonAndCheck("GET", f"/repos/{repo}/actions/jobs/{run_id}/logs")
        except Exception:  # noqa: BLE001
            return ""
        # GitHub returns plain text for logs, not JSON. PyGithub's
        # requestJsonAndCheck tolerates non-JSON via the body being a
        # str. Take the last ~80 lines.
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8", errors="replace")
        if not isinstance(body, str):
            return ""
        lines = body.splitlines()
        return "\n".join(lines[-80:])

    @swallow_errors(default=[], message="github list_recent_commits")
    def list_recent_commits(self, repo: str, *, since_days: int = 30, max_count: int = 200) -> List[Commit]:
        """List commits with their changed-file lists. Used by the
        code-hotspots extractor to build a co-change matrix."""
        from datetime import datetime, timedelta, timezone

        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pages = max(1, (max_count // 100) + 1)
        rows = GithubApi.get_paginated(f"/repos/{repo}/commits?since={since}", max_pages=pages)
        out: List[Commit] = []
        for r in rows[:max_count]:
            sha = str(r.get("sha") or "")
            if not sha:
                continue
            # GitHub's commit-list endpoint doesn't include the file
            # list — that requires `/commits/{sha}` per commit. To keep
            # the round-trip count bounded we only fetch files for the
            # first 50 commits.
            files: List[str] = []
            if len(out) < 50:
                detail = GithubApi.get_json(f"/repos/{repo}/commits/{sha}")
                if isinstance(detail, dict):
                    for f in detail.get("files") or []:
                        path = f.get("filename") or ""
                        if path:
                            files.append(path)
            commit_data = r.get("commit") or {}
            out.append(
                Commit(
                    sha=sha,
                    author=(commit_data.get("author") or {}).get("name") or "",
                    message=(commit_data.get("message") or "").splitlines()[0][:200] if commit_data.get("message") else "",
                    created_at=(commit_data.get("author") or {}).get("date") or "",
                    file_paths=files,
                )
            )
        return out

    @staticmethod
    def _to_pull(p: Dict[str, Any]) -> PullRequest:
        return PullRequest(
            number=int(p.get("number") or 0),
            title=(p.get("title") or "")[:200],
            author=(p.get("user") or {}).get("login") or "",
            is_draft=bool(p.get("draft")),
            head_ref=(p.get("head") or {}).get("ref") or "",
            base_ref=(p.get("base") or {}).get("ref") or "",
            review_comment_count=int(p.get("review_comments") or 0),
            created_at=p.get("created_at") or "",
            merged_at=p.get("merged_at") or "",
            requested_reviewers=[(r.get("login") or "") for r in (p.get("requested_reviewers") or [])],
        )
