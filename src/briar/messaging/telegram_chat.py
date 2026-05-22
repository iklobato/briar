"""Telegram chat writer — addressed sends to one chat.

Distinct from `NotificationSink.TelegramSink` even though both wrap
the same Bot API. The sink is for OPS alerts (fan-out by env var,
fire-and-forget on extract failure); the writer is for AGENT-driven
addressed messages (LLM emits `send_message(channel="ops_chat",
body="...")` and this picks up the chat ID from the company config).

Reads chat ID from ``TELEGRAM_<COMPANY>_CHAT_ID`` by default; a
per-binding ``config: {chat_env: TELEGRAM_X_OTHER_CHAT}`` lets a
single company have multiple named telegram channels (`ops`,
`engineering`)."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.messaging._writer import MessageWriter, SendResult


log = logging.getLogger(__name__)


class TelegramChatWriter(MessageWriter):
    kind = "telegram-chat"

    def __init__(self, *, company: str = "", config: Dict[str, Any] = None) -> None:
        self._company = company
        self._config = config or {}
        self._token = CredEnv.TELEGRAM_BOT_TOKEN.read()
        # Resolve chat ID: config override > per-company env var default
        chat_env = self._config.get("chat_env") or (CredEnv.TELEGRAM_CHAT_ID.for_company(company) if company else "")
        self._chat_id = os.environ.get(chat_env, "") if chat_env else ""

    def is_available(self) -> bool:
        return bool(self._token and self._chat_id)

    @swallow_errors(default=SendResult(ok=False, detail="exception"), message="telegram-chat send")
    def send(self, *, target: str, body: str, **extras: Any) -> SendResult:
        # `target` is ignored unless explicitly set to a chat ID (per-
        # message override). Default: use the binding's chat.
        chat_id = target or self._chat_id
        if not self._token or not chat_id:
            return SendResult(ok=False, detail="telegram missing token or chat_id")
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": body, "parse_mode": "Markdown"}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not payload.get("ok"):
            return SendResult(ok=False, detail=str(payload.get("description") or payload))
        ref = str(((payload.get("result") or {}).get("message_id") or ""))
        return SendResult(ok=True, ref=ref)

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        out = ["TELEGRAM_BOT_TOKEN"]
        if company:
            out.append(CredEnv.TELEGRAM_CHAT_ID.for_company(company))
        return out
