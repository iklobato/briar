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

from briar.extract._gh import GithubApi
from briar.extract._provider import (
    CiRun,
    Deployment,
    Environment,
    PullRequest,
    RepositoryProvider,
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
