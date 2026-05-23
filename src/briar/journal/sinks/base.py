"""`JournalSink` — Strategy contract for publish destinations.

One verb: ``publish(session)``. Sinks are fan-out, fire-and-forget at
the journal-facade level — a failure in one sink does not prevent the
next from being attempted (same resilience pattern as `NotificationSink`
in ``briar.notify``).

Each concrete sink owns its native render. Notion gets Notion blocks;
Slack gets Block Kit; FileSink writes markdown. Format reuse across
destinations turned out to be a non-goal — every API has a different
content model, and a separate renderer-strategy registry would only add
indirection without payoff."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from briar.journal.models import Session


class JournalSink(ABC):
    """Strategy contract. Subclasses register themselves into
    `JOURNAL_SINKS` (see this package's ``__init__``)."""

    name: ClassVar[str] = ""

    @abstractmethod
    def is_available(self) -> bool:
        """True iff the sink has the creds + config it needs. Same
        contract as `NotificationSink.is_available`."""

    @abstractmethod
    def publish(self, session: Session) -> bool:
        """Best-effort publish. Returns False on failure (already
        logged); raises only on programmer errors (e.g. an open
        session was passed in)."""
