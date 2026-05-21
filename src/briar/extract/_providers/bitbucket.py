"""Bitbucket Cloud `RepositoryProvider`.

Backed by `atlassian-python-api`'s Cloud client
(https://atlassian-python-api.readthedocs.io/bitbucket.html). The
library handles auth, pagination, retry, and typed PR / Pipeline /
Environment models — the work in this file is one thing: translate
its objects into the `_provider` dataclasses the extractors consume.

Auth: per-company. ``BITBUCKET_<COMPANY>_USERNAME`` +
``BITBUCKET_<COMPANY>_APP_PASSWORD`` + ``BITBUCKET_<COMPANY>_WORKSPACE``
(see ``CredEnv``). Empty company means generic provider —
``is_available()`` returns False and the extractor short-circuits.

Repo address convention: callers pass ``<workspace>/<repo_slug>`` to
match the GitHub convention; if a bare ``<repo_slug>`` is supplied,
the env-var workspace is used as the prefix."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.extract._provider import (
    CiRun,
    Deployment,
    Environment,
    PullRequest,
    RepositoryProvider,
)


log = logging.getLogger(__name__)


class BitbucketProvider(RepositoryProvider):
    kind = "bitbucket"
    BASE = "https://api.bitbucket.org/"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._username = CredEnv.BITBUCKET_USERNAME.read(company=company) if company else ""
        self._app_password = CredEnv.BITBUCKET_APP_PASSWORD.read(company=company) if company else ""
        self._workspace_slug = CredEnv.BITBUCKET_WORKSPACE.read(company=company) if company else ""
        self._client = None

    def _cloud(self):
        """Lazy-construct the Cloud client. The library doesn't
        authenticate on construction — first network call happens when
        an extractor verb is invoked."""
        if self._client is None:
            from atlassian.bitbucket.cloud import Cloud

            self._client = Cloud(
                url=self.BASE,
                username=self._username,
                password=self._app_password,
            )
        return self._client

    def _resolve_addr(self, repo: str) -> Tuple[str, str]:
        """Split ``repo`` into ``(workspace_slug, repo_slug)``. Accepts
        either ``<workspace>/<slug>`` (matches GitHub `owner/repo`
        convention) or a bare ``<slug>`` (uses the configured workspace
        env var)."""
        if "/" in repo:
            workspace_slug, _, repo_slug = repo.partition("/")
            return workspace_slug, repo_slug
        if not self._workspace_slug:
            raise RuntimeError(f"BitbucketProvider: repo {repo!r} is bare and no BITBUCKET_<COMPANY>_WORKSPACE is set")
        return self._workspace_slug, repo

    def _repo(self, repo: str):
        workspace_slug, repo_slug = self._resolve_addr(repo)
        return self._cloud().workspaces.get(workspace_slug).repositories.get(repo_slug)

    def is_available(self) -> bool:
        return bool(self._username and self._app_password and self._workspace_slug)

    @swallow_errors(default=[], message="bitbucket list_pulls")
    def list_pulls(self, repo: str, *, state: str, max_count: int) -> List[PullRequest]:
        from atlassian.bitbucket.cloud.repositories.pullRequests import PullRequest as BBPullRequest

        bb_state = BBPullRequest.STATE_MERGED if state == "merged" else BBPullRequest.STATE_OPEN
        bb_repo = self._repo(repo)
        out: List[PullRequest] = []
        for pr in bb_repo.pullrequests.each(q=f'state="{bb_state}"'):
            out.append(self._to_pull(pr, state=state))
            if len(out) >= max_count:
                break
        return out

    @swallow_errors(default=[], message="bitbucket list_environments")
    def list_environments(self, repo: str) -> List[Environment]:
        bb_repo = self._repo(repo)
        out: List[Environment] = []
        for env in bb_repo.deployment_environments.each():
            data: Dict[str, Any] = getattr(env, "data", {}) or {}
            out.append(
                Environment(
                    name=str(data.get("name") or getattr(env, "name", "") or ""),
                    protection_rule_count=int(data.get("restrictions_count") or 0),
                    url=str(data.get("self_uri") or ""),
                )
            )
        return out

    @swallow_errors(default=[], message="bitbucket list_deployments")
    def list_deployments(self, repo: str, *, limit: int) -> List[Deployment]:
        bb_repo = self._repo(repo)
        envelope = bb_repo.get("deployments/", params={"pagelen": min(limit, 100)})
        values = (envelope or {}).get("values", []) if isinstance(envelope, dict) else []
        out: List[Deployment] = []
        for d in values[:limit]:
            env_obj = d.get("environment") or {}
            release = d.get("release") or {}
            commit = release.get("commit") or {}
            deployer = d.get("deployer") or {}
            out.append(
                Deployment(
                    id=str(d.get("uuid") or ""),
                    environment=str(env_obj.get("name") or ""),
                    sha=str(commit.get("hash") or "")[:7],
                    creator=str(deployer.get("display_name") or ""),
                    created_at=str(d.get("created_on") or ""),
                )
            )
        return out

    @swallow_errors(default=[], message="bitbucket list_ci_runs")
    def list_ci_runs(self, repo: str, *, limit: int) -> List[CiRun]:
        bb_repo = self._repo(repo)
        out: List[CiRun] = []
        for p in bb_repo.pipelines.each(sort="-created_on"):
            data: Dict[str, Any] = getattr(p, "data", {}) or {}
            target = data.get("target") or {}
            state = data.get("state") or {}
            result = state.get("result") if isinstance(state, dict) else {}
            out.append(
                CiRun(
                    name=str(data.get("build_number") or ""),
                    status=str(state.get("name", "") if isinstance(state, dict) else ""),
                    conclusion=str((result or {}).get("name", "")),
                    head_branch=str(target.get("ref_name") or "") if isinstance(target, dict) else "",
                    created_at=str(data.get("created_on") or ""),
                )
            )
            if len(out) >= limit:
                break
        return out

    @swallow_errors(default="", message="bitbucket read_file")
    def read_file(self, repo: str, path: str) -> str:
        bb_repo = self._repo(repo)
        data: Dict[str, Any] = bb_repo.data or {}
        default_branch = (data.get("mainbranch") or {}).get("name") or "main"
        resp = bb_repo.get(f"src/{default_branch}/{path}", not_json_response=True)
        if isinstance(resp, (bytes, bytearray)):
            return resp.decode("utf-8", errors="replace")
        if isinstance(resp, str):
            return resp
        return ""

    @staticmethod
    def _to_pull(pr, *, state: str) -> PullRequest:
        """Translate one library `PullRequest` object into the
        provider-neutral dataclass."""
        author = pr.author
        return PullRequest(
            number=int(getattr(pr, "id", 0) or 0),
            title=(getattr(pr, "title", "") or "")[:200],
            author=(getattr(author, "display_name", "") or getattr(author, "nickname", "") or "") if author else "",
            is_draft=False,
            head_ref=str(getattr(pr, "source_branch", "") or ""),
            base_ref=str(getattr(pr, "destination_branch", "") or ""),
            review_comment_count=int(getattr(pr, "comment_count", 0) or 0),
            created_at=str(getattr(pr, "created_on", "") or ""),
            merged_at=(str(getattr(pr, "updated_on", "") or "")) if state == "merged" else "",
            requested_reviewers=[(getattr(r, "display_name", "") or getattr(r, "nickname", "") or "") for r in (getattr(pr, "reviewers", None) or [])],
        )
