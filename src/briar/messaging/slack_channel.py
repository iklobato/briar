"""Slack channel writer — addressed POST to one channel.

Two auth modes:
- **Incoming webhook** (default): the webhook URL itself is the
  credential. Per-binding override via ``config: {webhook_env: ...}``
  picks a different env var; otherwise falls back to
  ``SLACK_<COMPANY>_WEBHOOK_URL``.
- **chat.postMessage** (future): would require a Slack app + bot
  token, lets you address by channel ID at send time. Not implemented
  here — webhook covers the common case."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.messaging._writer import MessageWriter, SendResult


log = logging.getLogger(__name__)


class SlackChannelWriter(MessageWriter):
    kind = "slack-channel"

    def __init__(self, *, company: str = "", config: Dict[str, Any] = None) -> None:
        self._company = company
        self._config = config or {}
        webhook_env = self._config.get("webhook_env")
        if webhook_env:
            self._webhook_url = os.environ.get(webhook_env, "")
        elif company:
            self._webhook_url = CredEnv.SLACK_WEBHOOK_URL.read(company=company)
        else:
            self._webhook_url = ""

    def is_available(self) -> bool:
        return bool(self._webhook_url)

    @swallow_errors(default=SendResult(ok=False, detail="exception"), message="slack-channel send")
    def send(self, *, target: str, body: str, **extras: Any) -> SendResult:
        # `target` is unused for webhook mode — the URL is channel-bound.
        # Caller can pass `extras["title"]` to prefix with a Markdown header.
        if not self._webhook_url:
            return SendResult(ok=False, detail="slack webhook URL missing")
        title = extras.get("title", "")
        text = f"*{title}*\n{body}" if title else body
        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            self._webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body_text = resp.read().decode("utf-8", errors="replace").strip()
        if body_text != "ok":
            return SendResult(ok=False, detail=body_text[:200])
        return SendResult(ok=True)

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        # Per-binding `webhook_env` overrides are runtime-only; the
        # doctor can't see them. Report the default convention.
        if not company:
            return []
        return [CredEnv.SLACK_WEBHOOK_URL.for_company(company)]
