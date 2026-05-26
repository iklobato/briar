"""Jira ticket-comment writer.

Adds a comment to a Jira ticket. Backed by the same
``atlassian-python-api`` Jira client that `JiraTracker` uses for
reads, so creds are the per-company JIRA_<COMPANY>_{URL,EMAIL,TOKEN}
already documented in `CredEnv`."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.messaging._writer import MessageWriter, SendResult, with_ai_prefix


log = logging.getLogger(__name__)


class JiraCommentWriter(MessageWriter):
    kind = "jira-comment"

    def __init__(self, *, company: str = "", config: Dict[str, Any] = None) -> None:
        self._company = company
        self._config = config or {}
        self._url = CredEnv.JIRA_URL.read(company=company) if company else ""
        self._email = CredEnv.JIRA_EMAIL.read(company=company) if company else ""
        self._token = CredEnv.JIRA_TOKEN.read(company=company) if company else ""
        self._client = None

    def _jira(self):
        if self._client is None:
            from atlassian import Jira

            self._client = Jira(url=self._url, username=self._email, password=self._token, cloud=True)
        return self._client

    def is_available(self) -> bool:
        return bool(self._url and self._email and self._token)

    @swallow_errors(default=SendResult(ok=False, detail="exception"), message="jira-comment send")
    def send(self, *, target: str, body: str, **extras: Any) -> SendResult:
        if not self.is_available():
            return SendResult(ok=False, detail="jira creds missing")
        if not target:
            return SendResult(ok=False, detail="jira-comment requires target=<TICKET-KEY>")
        body = with_ai_prefix(body)
        # atlassian-python-api: client.issue_add_comment(issue_key, comment)
        resp = self._jira().issue_add_comment(target, body)
        if not isinstance(resp, dict):
            return SendResult(ok=False, detail=f"jira returned non-dict: {resp!r}")
        comment_id = str(resp.get("id") or "")
        return SendResult(ok=True, ref=comment_id)

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        if not company:
            return []
        return [
            CredEnv.JIRA_URL.for_company(company),
            CredEnv.JIRA_EMAIL.for_company(company),
            CredEnv.JIRA_TOKEN.for_company(company),
        ]
