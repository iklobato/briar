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

For Bitbucket workspace/repository access tokens (``ATCTT…`` prefix), set
``BITBUCKET_<COMPANY>_USERNAME=x-token-auth`` and put the token in
``BITBUCKET_<COMPANY>_APP_PASSWORD``. The provider auto-detects the sentinel
and switches to Bearer auth (workspace tokens reject HTTP basic).

Repo address convention: callers pass ``<workspace>/<repo_slug>`` to
match the GitHub convention; if a bare ``<repo_slug>`` is supplied,
the env-var workspace is used as the prefix."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
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

            if self._username == "x-token-auth":
                # Workspace/repository access tokens reject basic auth.
                self._client = Cloud(url=self.BASE, token=self._app_password, timeout=30)
            else:
                self._client = Cloud(
                    url=self.BASE,
                    username=self._username,
                    password=self._app_password,
                    timeout=30,
                )
        return self._client

    # ---- clone + auth seam (lifted from former BitbucketRepoCloner) ------

    def resolve_token(self) -> str:
        if not self._company:
            return ""
        return (CredEnv.BITBUCKET_APP_PASSWORD.read(company=self._company) or "").strip()

    def clone_url(self, owner: str, repo: str) -> str:
        return f"https://bitbucket.org/{owner}/{repo}.git"

    def authed_clone_url(self, owner: str, repo: str, token: str) -> str:
        # Bitbucket's workspace-token auth convention: `x-token-auth`.
        return f"https://x-token-auth:{token}@bitbucket.org/{owner}/{repo}.git"

    def pr_creation_recipe(self, *, owner: str, repo: str, branch: str) -> str:
        env_token = f"BITBUCKET_{self._company.upper().replace('-', '_')}_APP_PASSWORD"
        return (
            "  6. Open a draft PR via the Bitbucket v2 API. The workspace access token is in env var "
            f"`{env_token}`. Auth: `-u 'x-token-auth:${env_token}'`. Endpoint: "
            f"`POST https://api.bitbucket.org/2.0/repositories/{owner}/{repo}/pullrequests`. "
            f"Body JSON fields: `title`, `description`, `source.branch.name` (= `{branch}`), `draft: true`. "
            "The response's `links.html.href` is the PR URL.\n"
            "  7. End your output with the PR URL on its own line. No fictitious URLs — if the curl fails, "
            "surface the error verbatim.\n"
        )

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

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        if not company:
            return []
        return [
            CredEnv.BITBUCKET_USERNAME.for_company(company),
            CredEnv.BITBUCKET_APP_PASSWORD.for_company(company),
            CredEnv.BITBUCKET_WORKSPACE.for_company(company),
        ]

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

    @swallow_errors(default=None, message="bitbucket get_pull")
    def get_pull(self, repo: str, number: int) -> PullRequest:
        bb_repo = self._repo(repo)
        pr = bb_repo.pullrequests.get(number)
        # Bitbucket state vocabulary: OPEN | MERGED | DECLINED | SUPERSEDED
        state = "merged" if getattr(pr, "is_merged", False) else "open"
        return self._to_pull(pr, state=state)

    @swallow_errors(default=[], message="bitbucket list_pr_comments")
    def list_pr_comments(self, repo: str, number: int) -> List[ReviewComment]:
        bb_repo = self._repo(repo)
        pr = bb_repo.pullrequests.get(number)
        out: List[ReviewComment] = []
        for c in pr.comments():
            data = getattr(c, "data", {}) or {}
            inline = data.get("inline") or {}
            content = (data.get("content") or {}).get("raw") or ""
            user = data.get("user") or {}
            out.append(
                ReviewComment(
                    id=str(data.get("id") or ""),
                    author=str(user.get("display_name") or user.get("nickname") or ""),
                    body=str(content)[:1500],
                    file_path=str(inline.get("path") or "") if isinstance(inline, dict) else "",
                    line=int(inline.get("to") or inline.get("from") or 0) if isinstance(inline, dict) else 0,
                    is_resolved=False,
                    created_at=str(data.get("created_on") or ""),
                )
            )
        return out

    @swallow_errors(default=[], message="bitbucket list_ci_failures")
    def list_ci_failures(self, repo: str, number: int) -> List[CiFailure]:
        """For the PR's head commit, find FAILED Bitbucket Pipelines
        and the FAILED steps inside each, then fetch a short log tail
        per failure.

        Three round-trips per failure:
          1. `GET /repositories/{ws}/{slug}/pullrequests/{n}` for head sha
          2. `GET /pipelines/?target.commit.hash={sha}` to find runs
          3. per failed pipeline: `GET /pipelines/{uuid}/steps/` for steps
          4. per failed step: `GET /pipelines/{uuid}/steps/{step_uuid}/log`
        """
        bb_repo = self._repo(repo)
        pr = bb_repo.pullrequests.get(number)
        head_sha = ""
        # Library may or may not expose source.commit cleanly — try
        # attribute access first, fall back to the raw dict.
        source = getattr(pr, "source", None)
        if source is not None:
            commit = getattr(source, "commit", None)
            head_sha = str(getattr(commit, "hash", "") or "") if commit is not None else ""
        if not head_sha:
            data: Dict[str, Any] = getattr(pr, "data", {}) or {}
            head_sha = str(((data.get("source") or {}).get("commit") or {}).get("hash") or "")
        if not head_sha:
            return []
        envelope = bb_repo.get(
            "pipelines/",
            params={"target.commit.hash": head_sha, "pagelen": 50, "sort": "-created_on"},
        )
        pipelines = (envelope or {}).get("values", []) if isinstance(envelope, dict) else []
        out: List[CiFailure] = []
        for p in pipelines:
            if not self._pipeline_failed(p):
                continue
            pipeline_uuid = str(p.get("uuid") or "").strip("{}")
            if not pipeline_uuid:
                continue
            pipeline_url = ""
            links = p.get("links") if isinstance(p, dict) else None
            if isinstance(links, dict):
                pipeline_url = str((links.get("html") or {}).get("href") or "")
            workflow_label = f"pipeline #{p.get('build_number') or pipeline_uuid[:8]}"
            steps_env = bb_repo.get(f"pipelines/{pipeline_uuid}/steps/")
            steps = (steps_env or {}).get("values", []) if isinstance(steps_env, dict) else []
            for s in steps:
                if not self._pipeline_failed(s):
                    continue
                step_uuid = str(s.get("uuid") or "").strip("{}")
                step_name = str(s.get("name") or "step")
                out.append(
                    CiFailure(
                        workflow=workflow_label,
                        job=step_name,
                        step=step_name,
                        log_tail=self._tail_pipeline_step_log(bb_repo, pipeline_uuid, step_uuid),
                        url=pipeline_url,
                    )
                )
        return out

    @staticmethod
    def _pipeline_failed(node: Dict[str, Any]) -> bool:
        """True if the pipeline-or-step node's state.result is FAILED.
        Bitbucket nests the verdict under `state.result.name` for both
        the pipeline and each step, with `name` ∈
        {SUCCESSFUL, FAILED, ERROR, STOPPED, EXPIRED}."""
        state = node.get("state") if isinstance(node, dict) else None
        if not isinstance(state, dict):
            return False
        result = state.get("result") if isinstance(state.get("result"), dict) else {}
        name = str(result.get("name") or "").upper()
        return name in ("FAILED", "ERROR")

    @staticmethod
    def _tail_pipeline_step_log(bb_repo, pipeline_uuid: str, step_uuid: str) -> str:
        """Fetch the step log (plain text) and return the last ~80 lines.
        The endpoint streams text, not JSON — pass not_json_response=True
        through the atlassian-python-api helper."""
        if not pipeline_uuid or not step_uuid:
            return ""
        try:
            body = bb_repo.get(f"pipelines/{pipeline_uuid}/steps/{step_uuid}/log", not_json_response=True)
        except Exception:  # noqa: BLE001 — 404 / non-text bodies are common
            return ""
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8", errors="replace")
        if not isinstance(body, str):
            return ""
        lines = body.splitlines()
        return "\n".join(lines[-80:])

    @swallow_errors(default=[], message="bitbucket list_recent_commits")
    def list_recent_commits(self, repo: str, *, since_days: int = 30, max_count: int = 200) -> List[Commit]:
        bb_repo = self._repo(repo)
        out: List[Commit] = []
        for c in bb_repo.commits.each():
            data = getattr(c, "data", {}) or {}
            sha = str(data.get("hash") or "")
            if not sha:
                continue
            # Bitbucket commits include `parents` but file lists require
            # an additional /diffstat/{sha} call — populate for the
            # first 50 commits only.
            files: List[str] = []
            if len(out) < 50:
                try:
                    diffstat = bb_repo.get(f"diffstat/{sha}")
                    for f in (diffstat or {}).get("values", []) if isinstance(diffstat, dict) else []:
                        new_path = (f.get("new") or {}).get("path") or ""
                        if new_path:
                            files.append(new_path)
                except Exception:  # noqa: BLE001
                    pass
            author = (data.get("author") or {}).get("user") or {}
            out.append(
                Commit(
                    sha=sha,
                    author=str(author.get("display_name") or author.get("nickname") or ""),
                    message=(str(data.get("message") or "").splitlines() or [""])[0][:200],
                    created_at=str(data.get("date") or ""),
                    file_paths=files,
                )
            )
            if len(out) >= max_count:
                break
        return out

    @staticmethod
    def _field(pr: Any, name: str, default: Any = "") -> Any:
        """`getattr(pr, name)` falling back to `pr.data[name]`. The
        atlassian-python-api PR object surfaces some fields as
        attributes (varies by version) and the rest only via the raw
        `data` dict. Both ``draft`` and ``description`` are well-known
        offenders that drift between SDK releases — this helper hides
        the dance so `_to_pull` reads as a flat field list."""
        if hasattr(pr, name):
            value = getattr(pr, name, None)
            if value is not None:
                return value
        data: Dict[str, Any] = getattr(pr, "data", {}) or {}
        return data.get(name, default)

    @staticmethod
    def _person_display(person: Any) -> str:
        """Bitbucket "person" objects (author, reviewer) expose
        `display_name` on newer SDKs, `nickname` on older. One join."""
        if person is None:
            return ""
        return getattr(person, "display_name", "") or getattr(person, "nickname", "") or ""

    @classmethod
    def _to_pull(cls, pr, *, state: str) -> PullRequest:
        """Translate one library `PullRequest` object into the
        provider-neutral dataclass."""
        description_raw = cls._field(pr, "description")
        if isinstance(description_raw, dict):
            description = description_raw.get("raw", "") or ""
        else:
            description = description_raw or ""
        return PullRequest(
            number=int(cls._field(pr, "id", 0) or 0),
            title=str(cls._field(pr, "title", "") or "")[:200],
            author=cls._person_display(pr.author),
            is_draft=bool(cls._field(pr, "draft")),
            head_ref=str(cls._field(pr, "source_branch", "") or ""),
            base_ref=str(cls._field(pr, "destination_branch", "") or ""),
            review_comment_count=int(cls._field(pr, "comment_count", 0) or 0),
            created_at=str(cls._field(pr, "created_on", "") or ""),
            merged_at=(str(cls._field(pr, "updated_on", "") or "")) if state == "merged" else "",
            requested_reviewers=[cls._person_display(r) for r in (getattr(pr, "reviewers", None) or [])],
            body=str(description)[:5000],
        )
