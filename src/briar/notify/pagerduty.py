"""PagerDuty sink — Events API v2 (stdlib urllib).

Per-company integration key: ``PAGERDUTY_<COMPANY>_ROUTING_KEY``.
Each call creates a `trigger` event; PagerDuty handles deduplication
via the ``dedup_key`` derived from the title."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request

from briar.decorators import swallow_errors
from briar.notify._sink import NotificationSink


log = logging.getLogger(__name__)


_ENDPOINT = "https://events.pagerduty.com/v2/enqueue"


class PagerDutySink(NotificationSink):
    kind = "pagerduty"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        slug = company.upper().replace("-", "_") if company else ""
        self._routing_key = os.environ.get(f"PAGERDUTY_{slug}_ROUTING_KEY", "") if slug else ""

    def is_available(self) -> bool:
        return bool(self._routing_key)

    @swallow_errors(default=False, message="pagerduty send")
    def send(self, *, title: str, body: str) -> bool:
        if not self.is_available():
            log.warning("pagerduty send skipped — no routing key (company=%s)", self._company)
            return False
        # Dedup on title so a repeating failure doesn't create N incidents.
        dedup_key = hashlib.sha1(f"{self._company}:{title}".encode("utf-8")).hexdigest()[:16]
        payload = json.dumps(
            {
                "routing_key": self._routing_key,
                "event_action": "trigger",
                "dedup_key": dedup_key,
                "payload": {
                    "summary": title[:1024],
                    "source": f"briar:{self._company or 'unknown'}",
                    "severity": "warning",
                    "custom_details": {"body": body[:8192]},
                },
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            _ENDPOINT,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            response = json.loads(resp.read().decode("utf-8"))
        if response.get("status") != "success":
            log.warning("pagerduty send non-success: %s", response)
            return False
        return True
