"""Slack sink — stub.

Implement via stdlib ``urllib`` POST to an incoming webhook URL
(``SLACK_<COMPANY>_WEBHOOK_URL``). No external dep needed."""

from __future__ import annotations

from briar.env_vars import CredEnv
from briar.notify._sink import NotificationSink


class SlackSink(NotificationSink):
    kind = "slack"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._webhook_url = CredEnv.SLACK_WEBHOOK_URL.read(company=company) if company else ""

    def is_available(self) -> bool:
        return bool(self._webhook_url)

    def send(self, *, title: str, body: str) -> bool:
        raise NotImplementedError(
            "SlackSink.send is not implemented yet. POST to self._webhook_url with "
            "Content-Type: application/json and body "
            '{"text": f"*{title}*\\n{body}"} . Slack returns "ok" string on success.'
        )
