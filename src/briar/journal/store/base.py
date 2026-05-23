"""`JournalStore` — Strategy contract for the system-of-record.

The store is where closed sessions go to live. Always-on, queryable —
`briar journal show` and `briar journal export` read from it; sinks
(NotionSink, SlackSink, FileSink) are the *publish* fan-out and are a
separate concern (see `briar.journal.sinks`).

Backends implement `put` / `get` / `list`. The base intentionally does
NOT define `delete` — journal entries are append-only by policy; if
you need to scrub a session, drop it at the backend level (file rm,
pg DELETE) but don't make the front-door deletion a one-liner the
typical caller can reach for."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, List, Mapping, Optional

from briar.journal.models import Session


@dataclass(frozen=True)
class JournalStoreBinding:
    """Mirror of `briar.storage.StoreBinding` — keeps the pattern
    consistent across the codebase's storage families. ``config`` is
    a free-form bag the concrete backend interprets (e.g. file's
    ``root``, postgres's ``dsn_env``)."""

    store: str = "file"
    root: str = ""
    config: Mapping[str, str] = field(default_factory=dict)


@dataclass
class JournalRef:
    """One session, as returned by `list`. Cheap metadata — full
    content is fetched via `get(session_id)`."""

    session_id: str
    command: str
    target: str = ""
    started_at: str = ""
    ended_at: str = ""
    decision_count: int = 0


class JournalStore(ABC):
    """Strategy contract. Subclasses register themselves into the
    `JOURNAL_STORES` registry (one entry per backend)."""

    name: ClassVar[str] = ""

    @classmethod
    @abstractmethod
    def from_binding(cls, binding: JournalStoreBinding, *, default_root: Optional[Path] = None) -> "JournalStore":
        """Construct a store from a resolved binding."""

    @abstractmethod
    def put(self, session: Session) -> JournalRef:
        """Persist a closed session. Raises if the session isn't closed —
        an open session is mid-flight and shouldn't be a system-of-record
        artifact yet."""

    @abstractmethod
    def get(self, session_id: str) -> Optional[Session]:
        """Return the full session, or `None` when not found."""

    @abstractmethod
    def list(self, *, command_prefix: str = "", limit: int = 50) -> List[JournalRef]:
        """Enumerate stored sessions, newest first. ``command_prefix``
        filters on the recorded ``command`` (e.g. ``scaffold.``)."""
