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
import re
from typing import Any, Dict, List

from briar.decorators import swallow_errors
from briar.extract._gh import GithubApi
from briar.extract._provider import CiFailure, CiRun, Commit, Deployment, Environment, PullRequest, RepositoryProvider, ReviewComment

log = logging.getLogger(__name__)


# GitHub allows alphanumerics, dot, hyphen, underscore in owner/repo
# segments. Validate at the boundary so an oddly-escaped runbook value
# can't break out of the path segment in any of the f-string URLs below.
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_repo(repo: str) -> None:
    if not _REPO_RE.match(repo):
        raise ValueError(f"github repo must match owner/repo: got {repo!r}")


class GithubProvider(RepositoryProvider):
    kind = "github"

    def __init__(self, *, company: str = "") -> None:
        # `company` is part of the abstract signature but inert here —
        # GitHub credentials are workspace-wide.
        self._company = company

    def is_available(self) -> bool:
        return bool(GithubApi.auth_token())

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        # GITHUB_TOKEN is workspace-wide; the company arg is inert.
        return ["GITHUB_TOKEN"]

    # ---- clone + auth seam (lifted from former GithubRepoCloner) ---------

    def resolve_token(self) -> str:
        return GithubApi.auth_token()

    def clone_url(self, owner: str, repo: str) -> str:
        return f"https://github.com/{owner}/{repo}.git"

    def authed_clone_url(self, owner: str, repo: str, token: str) -> str:
        # GitHub's HTTPS auth convention: `x-access-token` as the username.
        return f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

    def pr_creation_recipe(self, *, owner: str, repo: str, branch: str) -> str:
        # owner/repo/branch are unused for the GitHub recipe — `gh pr create`
        # infers them from the worktree — but kept in the signature for
        # parity with other providers.
        return (
            "  6. Open a draft PR via `gh pr create --draft --title '<key>: <short>' "
            "--body '<plan + test plan + risks>'`.\n"
            "  7. End your output with the PR URL on its own line. No fictitious URLs "
            "— if `gh pr create` fails, surface the error.\n"
        )

    def list_pulls(self, repo: str, *, state: str, max_count: int) -> List[PullRequest]:
        # Contract: state is "open" | "merged". GitHub vocabulary is
        # "open" | "closed", and merged PRs are a subset of closed
        # where `merged_at` is not null. Translate at the boundary.
        _validate_repo(repo)
        gh_state = "closed" if state == "merged" else state
        pages_needed = max(1, (max_count // 100) + 1)
        path = f"/repos/{repo}/pulls?state={gh_state}&sort=updated&direction=desc"
        rows = GithubApi.get_paginated(path, max_pages=pages_needed)
        if state == "merged":
            rows = [r for r in rows if r.get("merged_at") is not None]
        return [self._to_pull(r) for r in rows[:max_count]]

    def list_environments(self, repo: str) -> List[Environment]:
        _validate_repo(repo)
        envelope = GithubApi.get_json(f"/repos/{repo}/environments")
        envs = envelope.get("environments", []) if isinstance(envelope, dict) else []
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
        _validate_repo(repo)
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
        _validate_repo(repo)
        envelope = GithubApi.get_json(f"/repos/{repo}/actions/runs?per_page={limit}")
        runs = envelope.get("workflow_runs", []) if isinstance(envelope, dict) else []
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
        _validate_repo(repo)
        try:
            resp = GithubApi.get_json(f"/repos/{repo}/contents/{path}")
        except Exception:  # noqa: BLE001 — 404 is the common case
            return ""
        if not isinstance(resp, dict) or resp.get("type") != "file":
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
        _validate_repo(repo)
        data = GithubApi.get_json(f"/repos/{repo}/pulls/{number}")
        if not isinstance(data, dict):
            return super().get_pull(repo, number)
        return self._to_pull(data)

    @swallow_errors(default=[], message="github list_pr_comments")
    def list_pr_comments(self, repo: str, number: int) -> List[ReviewComment]:
        """Returns inline review comments AND top-level issue comments
        AND review-summary comments for one PR. Three GitHub endpoints —
        `/pulls/{n}/comments` for review threads (file/line-level),
        `/issues/{n}/comments` for top-level Conversation-tab comments,
        and `/pulls/{n}/reviews` for review submissions ("Approved",
        "Request changes" with their body) — return different shapes;
        this method merges them into one ReviewComment list."""
        _validate_repo(repo)
        inline = GithubApi.get_paginated(f"/repos/{repo}/pulls/{number}/comments", max_pages=2) or []
        top_level = GithubApi.get_paginated(f"/repos/{repo}/issues/{number}/comments", max_pages=2) or []
        reviews = GithubApi.get_paginated(f"/repos/{repo}/pulls/{number}/reviews", max_pages=2) or []
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
        for r in reviews:
            review_body = (r.get("body") or "").strip()
            state = (r.get("state") or "").upper()
            # Skip pure-state reviews with no body and no decisive verdict —
            # they're noise (the "COMMENTED" no-body case is the user just
            # leaving inline comments without a wrapper, already covered above).
            if not review_body and state not in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                continue
            prefix = f"[{state}] " if state else ""
            out.append(
                ReviewComment(
                    id=str(r.get("id") or ""),
                    author=(r.get("user") or {}).get("login") or "",
                    body=(prefix + review_body)[:1500],
                    file_path="",
                    line=0,
                    is_resolved=False,
                    created_at=r.get("submitted_at") or "",
                )
            )
        return out

    @swallow_errors(default=[], message="github list_ci_failures")
    def list_ci_failures(self, repo: str, number: int) -> List[CiFailure]:
        """For the PR's head SHA, find failing check-runs and pull a
        short log tail per failure. GitHub's `/check-runs` endpoint
        gives status; the log requires a second call against
        `/actions/jobs/{job_id}/logs` (text, not JSON)."""
        _validate_repo(repo)
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
            # Check-runs carry their own `output.title` / `output.summary`
            # which is usually populated with the failing step's headline
            # (e.g. "pytest — 3 failed"). Cheaper than a follow-up
            # annotations fetch and gives the agent a useful label.
            output = run.get("output") or {}
            step_name = (output.get("title") or "").strip()
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

    # Cap on how many of the most-recent commits get their per-file
    # diff hydrated. GitHub's commit-list endpoint omits the file list
    # so each detail fetch is one extra round-trip — bound it for the
    # code-hotspots use case where N>50 adds no signal.
    _COMMIT_FILE_DETAIL_CAP = 50

    @staticmethod
    def _fetch_commit_files(repo: str, sha: str) -> List[str]:
        """Per-commit file-list fetch. Extracted from `list_recent_commits`
        so the per-commit IO is named and one-liner-callable. Returns
        empty list on any shape that isn't a dict-with-files."""
        detail = GithubApi.get_json(f"/repos/{repo}/commits/{sha}")
        if not isinstance(detail, dict):
            return []
        return [f.get("filename") or "" for f in (detail.get("files") or []) if f.get("filename")]

    @swallow_errors(default=[], message="github list_recent_commits")
    def list_recent_commits(self, repo: str, *, since_days: int = 30, max_count: int = 200) -> List[Commit]:
        """List commits with their changed-file lists. Used by the
        code-hotspots extractor to build a co-change matrix."""
        _validate_repo(repo)
        from datetime import datetime, timedelta, timezone

        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pages = max(1, (max_count // 100) + 1)
        rows = GithubApi.get_paginated(f"/repos/{repo}/commits?since={since}", max_pages=pages)
        out: List[Commit] = []
        for r in rows[:max_count]:
            sha = str(r.get("sha") or "")
            if not sha:
                continue
            files = self._fetch_commit_files(repo, sha) if len(out) < self._COMMIT_FILE_DETAIL_CAP else []
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
            # Cap PR description at the boundary — long PR bodies are common
            # and would otherwise eat the agent's context budget.
            body=(p.get("body") or "")[:5000],
        )
