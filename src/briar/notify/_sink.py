"""`NotificationSink` — Strategy contract.

One verb: ``send(title, body)``. Sinks are deliberately fire-and-forget;
the caller doesn't need to handle delivery success/failure differently
per sink. ``send`` returns ``True`` on best-effort success, ``False``
on failure (logged). No exceptions across the boundary."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar


log = logging.getLogger(__name__)


class NotificationSink(ABC):
    kind: ClassVar[str] = ""

    @abstractmethod
    def is_available(self) -> bool:
        """True iff the sink has the creds it needs."""

    @abstractmethod
    def send(self, *, title: str, body: str) -> bool:
        """Best-effort send. Returns False on failure (already logged)."""
