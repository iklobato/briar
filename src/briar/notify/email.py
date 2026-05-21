"""Email sink — stub.

Implement via stdlib ``smtplib`` + ``email.message.EmailMessage``.
Env vars: ``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USER``,
``SMTP_PASSWORD``, ``EMAIL_FROM``, per-company
``EMAIL_<COMPANY>_TO``."""

from __future__ import annotations

import os

from briar.notify._sink import NotificationSink


class EmailSink(NotificationSink):
    kind = "email"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._host = os.environ.get("SMTP_HOST", "")
        self._to = os.environ.get(f"EMAIL_{company.upper().replace('-', '_')}_TO", "") if company else ""

    def is_available(self) -> bool:
        return bool(self._host and self._to)

    def send(self, *, title: str, body: str) -> bool:
        raise NotImplementedError(
            "EmailSink.send is not implemented yet. Build via smtplib.SMTP(host, port).starttls().login(user, password).send_message(msg) "
            "with msg=EmailMessage(); msg['Subject']=title; msg.set_content(body); "
            "msg['From']=os.environ['EMAIL_FROM']; msg['To']=self._to."
        )
