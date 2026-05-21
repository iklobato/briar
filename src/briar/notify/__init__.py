"""Notification sinks — `NotificationSink` Strategy + Registry.

Used by `briar notify` (the CLI command) and by the scheduler when an
extractor fails repeatedly. Adding a new sink (Discord, PagerDuty,
webhook, …) = one file + one entry."""

from __future__ import annotations

from typing import Dict, Tuple, Type

from briar.errors import CliError
from briar.notify._sink import NotificationSink
from briar.notify.email import EmailSink
from briar.notify.pagerduty import PagerDutySink
from briar.notify.slack import SlackSink
from briar.notify.telegram import TelegramSink


SINKS: Dict[str, Type[NotificationSink]] = {
    cls.kind: cls
    for cls in (TelegramSink, SlackSink, EmailSink, PagerDutySink)
}


class NotificationRegistry:
    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(SINKS.keys())

    @classmethod
    def make(cls, kind: str, *, company: str = "") -> NotificationSink:
        sink_cls = SINKS.get(kind)
        if sink_cls is None:
            known = ", ".join(sorted(SINKS.keys()))
            raise CliError(f"unknown notification sink {kind!r}; known: {known}")
        return sink_cls(company=company)


make_sink = NotificationRegistry.make


__all__ = ["SINKS", "NotificationSink", "NotificationRegistry", "make_sink"]
