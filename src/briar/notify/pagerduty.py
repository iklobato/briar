"""PagerDuty sink — stub.

Implement via stdlib ``urllib`` POST to PagerDuty Events API v2:
``POST https://events.pagerduty.com/v2/enqueue`` with
``{routing_key, event_action: 'trigger', payload: {summary, source, severity}}``.

Env vars: ``PAGERDUTY_ROUTING_KEY`` (integration key) per company:
``PAGERDUTY_<COMPANY>_ROUTING_KEY``."""

from __future__ import annotations

import os

from briar.notify._sink import NotificationSink


class PagerDutySink(NotificationSink):
    kind = "pagerduty"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._routing_key = os.environ.get(f"PAGERDUTY_{company.upper().replace('-', '_')}_ROUTING_KEY", "") if company else ""

    def is_available(self) -> bool:
        return bool(self._routing_key)

    def send(self, *, title: str, body: str) -> bool:
        raise NotImplementedError(
            "PagerDutySink.send is not implemented yet. POST to "
            "https://events.pagerduty.com/v2/enqueue with body "
            '{"routing_key": self._routing_key, "event_action": "trigger", '
            '"payload": {"summary": title, "source": "briar", "severity": "warning", "custom_details": {"body": body}}}.'
        )
