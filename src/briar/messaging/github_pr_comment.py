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
from typing import Any, Dict, List, Optional, Tuple

from briar.decorators import swallow_errors
from briar.extract._gh import GithubApi
from briar.messaging._writer import MessageWriter, SendResult, parse_pr_target, with_ai_prefix


log = logging.getLogger(__name__)


class GithubPrCommentWriter(MessageWriter):
    kind = "github-pr-comment"

    def __init__(self, *, company: str = "", config: Optional[Dict[str, Any]] = None) -> None:
        # GITHUB_TOKEN is workspace-wide; company is inert.
        self._company = company
        self._config = config or {}

    def is_available(self) -> bool:
        return bool(GithubApi.auth_token())

    @swallow_errors(default=SendResult(ok=False, detail="exception"), message="github-pr-comment send")
    def send(self, *, target: str, body: str, **extras: Any) -> SendResult:
        repo, number = parse_pr_target(target, extras)
        if not repo or not number:
            return SendResult(ok=False, detail=f"github-pr-comment requires target=owner/repo#N; got {target!r}")
        body = with_ai_prefix(body)

        # Use PyGithub's public API instead of the name-mangled internal
        # `_Github__requester.requestJsonAndCheck` — the private form
        # breaks on every PyGithub upgrade and bypasses retry/ratelimit
        # handling the public path applies.
        client = GithubApi.client()
        repo_obj = client.get_repo(repo)
        pr = repo_obj.get_pull(number)

        file_path = extras.get("file_path")
        if file_path:
            commit_id = pr.head.sha or ""
            if not commit_id:
                return SendResult(ok=False, detail="github-pr-comment inline: could not resolve head SHA")
            line = int(extras.get("line") or 0) or 1
            commit_obj = repo_obj.get_commit(commit_id)
            review_comment = pr.create_review_comment(
                body=body,
                commit=commit_obj,
                path=file_path,
                line=line,
                side=extras.get("side", "RIGHT"),
            )
            return SendResult(ok=True, ref=str(review_comment.id))

        issue_comment = pr.create_issue_comment(body)
        return SendResult(ok=True, ref=str(issue_comment.id))

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        return ["GITHUB_TOKEN"]
