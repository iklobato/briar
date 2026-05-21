"""Slack sink — POST to an incoming webhook URL.

Uses stdlib ``urllib`` so no new dependency. The webhook URL itself
is the credential (no separate token); rotation = re-issuing the
webhook URL in Slack's admin UI."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.notify._sink import NotificationSink


log = logging.getLogger(__name__)


class SlackSink(NotificationSink):
    kind = "slack"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._webhook_url = CredEnv.SLACK_WEBHOOK_URL.read(company=company) if company else ""

    def is_available(self) -> bool:
        return bool(self._webhook_url)

    @swallow_errors(default=False, message="slack send")
    def send(self, *, title: str, body: str) -> bool:
        if not self.is_available():
            log.warning("slack send skipped — no webhook URL (company=%s)", self._company)
            return False
        payload = json.dumps({"text": f"*{title}*\n{body}"}).encode("utf-8")
        req = urllib.request.Request(
            self._webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body_text = resp.read().decode("utf-8", errors="replace").strip()
        if body_text != "ok":
            log.warning("slack send returned non-ok body: %r", body_text)
            return False
        return True
