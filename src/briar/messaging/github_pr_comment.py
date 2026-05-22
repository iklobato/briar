"""GitHub PR-comment writer.

Posts either:
- a TOP-LEVEL issue/PR comment (default), or
- an INLINE review-thread reply when ``extras["file_path"]`` is set.

``target`` is ``owner/repo#42`` (the #42 is the PR number). The PR
number can also be passed as ``extras["pr"]`` if the target is a
bare repo slug.

Backed by the same `GithubApi` PyGithub facade the reads use."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from briar.decorators import swallow_errors
from briar.extract._gh import GithubApi
from briar.messaging._writer import MessageWriter, SendResult


log = logging.getLogger(__name__)


class GithubPrCommentWriter(MessageWriter):
    kind = "github-pr-comment"

    def __init__(self, *, company: str = "", config: Dict[str, Any] = None) -> None:
        # GITHUB_TOKEN is workspace-wide; company is inert.
        self._company = company
        self._config = config or {}

    def is_available(self) -> bool:
        return bool(GithubApi.auth_token())

    @swallow_errors(default=SendResult(ok=False, detail="exception"), message="github-pr-comment send")
    def send(self, *, target: str, body: str, **extras: Any) -> SendResult:
        repo, number = self._parse_target(target, extras)
        if not repo or not number:
            return SendResult(ok=False, detail=f"github-pr-comment requires target=owner/repo#N; got {target!r}")

        file_path = extras.get("file_path")
        if file_path:
            # Inline review-thread reply requires the PR's head SHA +
            # a file/line anchor. The replies endpoint uses commit_id.
            pr = GithubApi.get_json(f"/repos/{repo}/pulls/{number}")
            commit_id = ((pr or {}).get("head") or {}).get("sha") or ""
            if not commit_id:
                return SendResult(ok=False, detail="github-pr-comment inline: could not resolve head SHA")
            line = int(extras.get("line") or 0)
            payload = {
                "body": body,
                "commit_id": commit_id,
                "path": file_path,
                "line": line or 1,
                "side": extras.get("side", "RIGHT"),
            }
            client = GithubApi.client()
            headers, resp = client._Github__requester.requestJsonAndCheck("POST", f"/repos/{repo}/pulls/{number}/comments", input=payload)
            comment_id = str((resp or {}).get("id") or "") if isinstance(resp, dict) else ""
            return SendResult(ok=True, ref=comment_id)

        # Top-level issue comment endpoint (the same one GitHub uses
        # for non-inline PR comments).
        client = GithubApi.client()
        headers, resp = client._Github__requester.requestJsonAndCheck("POST", f"/repos/{repo}/issues/{number}/comments", input={"body": body})
        comment_id = str((resp or {}).get("id") or "") if isinstance(resp, dict) else ""
        return SendResult(ok=True, ref=comment_id)

    @staticmethod
    def _parse_target(target: str, extras: Dict[str, Any]) -> Tuple[str, int]:
        """Accept `owner/repo#42` OR bare `owner/repo` + `extras["pr"]`."""
        if "#" in target:
            repo, _, n = target.rpartition("#")
            try:
                return repo, int(n)
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
        return ["GITHUB_TOKEN"]
