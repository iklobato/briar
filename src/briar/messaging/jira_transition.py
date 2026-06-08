"""Jira ticket-transition writer.

Transitions a Jira ticket to a target status. The status name comes
either from ``extras["status"]`` at send time or from the binding's
``config: {status: "Done"}`` default.

`body` is recorded as an optional resolution comment (Jira's
transitions can carry a comment field)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from briar.decorators import swallow_errors
from briar.messaging._jira_creds import JiraCreds
from briar.messaging._writer import MessageWriter, SendResult, with_ai_prefix

log = logging.getLogger(__name__)


class JiraTransitionWriter(MessageWriter):
    kind = "jira-transition"

    def __init__(self, *, company: str = "", config: Optional[Dict[str, Any]] = None) -> None:
        self._company = company
        self._config = config or {}
        self._default_status = str(self._config.get("status", ""))
        self._creds = JiraCreds.from_env(company)
        self._client = None

    def _jira(self):
        if self._client is None:
            self._client = self._creds.client()
        return self._client

    def is_available(self) -> bool:
        return self._creds.is_complete()

    @swallow_errors(default=SendResult(ok=False, detail="exception"), message="jira-transition send")
    def send(self, *, target: str, body: str, **extras: Any) -> SendResult:
        if not self.is_available():
            return SendResult(ok=False, detail="jira creds missing")
        if not target:
            return SendResult(ok=False, detail="jira-transition requires target=<TICKET-KEY>")
        status = extras.get("status") or self._default_status
        if not status:
            return SendResult(ok=False, detail="jira-transition requires extras.status or binding config.status")
        # The library exposes `set_issue_status` which wraps the
        # /transitions endpoint + the transition-id resolution. It has no
        # `comment` parameter; a resolution note rides along on the same
        # transition POST via the `update` field, which is exactly what
        # Jira's transitions API expects (update.comment[].add.body).
        # Mark it with [AI] per CLAUDE.md for operator-impersonated text.
        update = None
        if body:
            update = {"comment": [{"add": {"body": with_ai_prefix(body)}}]}
        result = self._jira().set_issue_status(target, status, update=update)
        if result is None:
            return SendResult(ok=True, ref=f"{target}→{status}")
        # Atlassian's response shape varies — treat any non-None as ok.
        return SendResult(ok=True, ref=str(result)[:200])

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        return JiraCreds.required_env_vars(company)
