"""Telegram sink — the only fully-implemented `NotificationSink`.

Uses the Bot API via stdlib ``urllib`` so we don't add a dependency.
Auth: ``TELEGRAM_BOT_TOKEN`` (workspace-wide) + per-company chat id
``TELEGRAM_<COMPANY>_CHAT_ID`` — chat IDs are tenant-scoped because
different companies post to different channels."""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.notify._sink import NotificationSink

log = logging.getLogger(__name__)


# Bot API host. Module-level so tests can point the real urllib send at a
# wire-level mock (the live host has no env/base-url override of its own).
_API_BASE = "https://api.telegram.org"


class TelegramSink(NotificationSink):
    kind = "telegram"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._token = CredEnv.TELEGRAM_BOT_TOKEN.read()
        self._chat_id = CredEnv.TELEGRAM_CHAT_ID.read(company=company) if company else ""

    def is_available(self) -> bool:
        return bool(self._token and self._chat_id)

    @swallow_errors(default=False, message="telegram send")
    def send(self, *, title: str, body: str) -> bool:
        if not self.is_available():
            log.warning("telegram send skipped — no token or chat_id (company=%s)", self._company)
            return False
        text = f"*{title}*\n\n{body}"
        url = f"{_API_BASE}/bot{self._token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": self._chat_id, "text": text, "parse_mode": "Markdown"}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not payload.get("ok"):
            log.warning("telegram send failed: %s", payload.get("description") or payload)
            return False
        return True
