"""Email sink — stdlib smtplib + EmailMessage.

Env-var schema (workspace-wide + per-company recipient):
- ``SMTP_HOST`` / ``SMTP_PORT`` (default 587)
- ``SMTP_USER`` / ``SMTP_PASSWORD``
- ``SMTP_STARTTLS`` (default "true"; set to "false" for plain SMTP)
- ``EMAIL_FROM``  — sender address
- ``EMAIL_<COMPANY>_TO`` — recipient(s) for this company, comma-separated"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import List

from briar.decorators import swallow_errors
from briar.notify._sink import NotificationSink


log = logging.getLogger(__name__)


class EmailSink(NotificationSink):
    kind = "email"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._host = os.environ.get("SMTP_HOST", "")
        self._port = int(os.environ.get("SMTP_PORT") or "587")
        self._user = os.environ.get("SMTP_USER", "")
        self._password = os.environ.get("SMTP_PASSWORD", "")
        self._starttls = os.environ.get("SMTP_STARTTLS", "true").lower() != "false"
        self._from = os.environ.get("EMAIL_FROM", self._user)
        slug = company.upper().replace("-", "_") if company else ""
        raw_to = os.environ.get(f"EMAIL_{slug}_TO", "") if slug else ""
        self._to: List[str] = [a.strip() for a in raw_to.split(",") if a.strip()]

    def is_available(self) -> bool:
        return bool(self._host and self._from and self._to)

    @swallow_errors(default=False, message="email send")
    def send(self, *, title: str, body: str) -> bool:
        if not self.is_available():
            log.warning("email send skipped — missing SMTP_HOST / EMAIL_FROM / EMAIL_%s_TO", self._company.upper())
            return False
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = self._from
        msg["To"] = ", ".join(self._to)
        msg.set_content(body)
        with smtplib.SMTP(self._host, self._port, timeout=10) as client:
            if self._starttls:
                client.starttls()
            if self._user and self._password:
                client.login(self._user, self._password)
            client.send_message(msg)
        return True
