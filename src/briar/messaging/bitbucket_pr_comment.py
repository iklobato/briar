"""Bitbucket Cloud PR-comment writer.

Top-level OR inline (with `extras["file_path"]`). `target` is
``workspace/repo#42``. Uses the same atlassian-python-api Cloud
client + per-company `BITBUCKET_<COMPANY>_*` creds as
`BitbucketProvider`."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.messaging._writer import MessageWriter, SendResult


log = logging.getLogger(__name__)


class BitbucketPrCommentWriter(MessageWriter):
    kind = "bitbucket-pr-comment"
    BASE = "https://api.bitbucket.org/"

    def __init__(self, *, company: str = "", config: Dict[str, Any] = None) -> None:
        self._company = company
        self._config = config or {}
        self._username = CredEnv.BITBUCKET_USERNAME.read(company=company) if company else ""
        self._app_password = CredEnv.BITBUCKET_APP_PASSWORD.read(company=company) if company else ""
        self._workspace_slug = CredEnv.BITBUCKET_WORKSPACE.read(company=company) if company else ""
        self._client = None

    def _cloud(self):
        if self._client is None:
            from atlassian.bitbucket.cloud import Cloud

            if self._username == "x-token-auth":
                self._client = Cloud(url=self.BASE, token=self._app_password)
            else:
                self._client = Cloud(url=self.BASE, username=self._username, password=self._app_password)
        return self._client

    def is_available(self) -> bool:
        return bool(self._username and self._app_password and self._workspace_slug)

    @swallow_errors(default=SendResult(ok=False, detail="exception"), message="bitbucket-pr-comment send")
    def send(self, *, target: str, body: str, **extras: Any) -> SendResult:
        repo_addr, number = self._parse_target(target, extras)
        if not repo_addr or not number:
            return SendResult(ok=False, detail=f"bitbucket-pr-comment requires target=workspace/repo#N; got {target!r}")
        workspace_slug, _, repo_slug = repo_addr.partition("/")
        if not workspace_slug or not repo_slug:
            workspace_slug = self._workspace_slug
            repo_slug = repo_addr
        bb_repo = self._cloud().workspaces.get(workspace_slug).repositories.get(repo_slug)

        # PR comment payload — inline if file_path + line are set
        payload: Dict[str, Any] = {"content": {"raw": body}}
        file_path = extras.get("file_path")
        if file_path:
            payload["inline"] = {
                "path": file_path,
                "to": int(extras.get("line") or 1),
            }
        resp = bb_repo.post(f"pullrequests/{number}/comments", data=payload)
        comment_id = str((resp or {}).get("id") or "") if isinstance(resp, dict) else ""
        return SendResult(ok=True, ref=comment_id)

    @staticmethod
    def _parse_target(target: str, extras: Dict[str, Any]) -> Tuple[str, int]:
        if "#" in target:
            addr, _, n = target.rpartition("#")
            try:
                return addr, int(n)
            except ValueError:
                return "", 0
        n_extras = extras.get("pr")
        if target and n_extras is not None:
            try:
                return target, int(n_extras)
            except (TypeError, ValueError):
                return "", 0
        return "", 0

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        if not company:
            return []
        return [
            CredEnv.BITBUCKET_USERNAME.for_company(company),
            CredEnv.BITBUCKET_APP_PASSWORD.for_company(company),
            CredEnv.BITBUCKET_WORKSPACE.for_company(company),
        ]
