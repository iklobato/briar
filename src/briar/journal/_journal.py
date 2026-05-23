"""`Journal` façade — accepts decision records, persists, fans out.

Two responsibilities, kept separate inside the class:
- **Store** (write-of-record): the configured `JournalStore` instance.
  A failure here IS an error — the system of record is meant to survive
  publish failures.
- **Sinks** (publish fan-out): zero-or-more `JournalSink` instances.
  A failure in any one sink is logged and the next sink is still tried;
  same resilience contract as `NotificationSink` already has in this
  codebase.

A `_NoOpJournal` is exposed as the module-level default so call sites
in instrumented code don't need to guard for "is journaling active."
Test code and the CLI entrypoint swap in a real `Journal` via
`set_active_journal(...)`."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, List, Optional, Sequence

from briar.journal.models import DecisionEvent, Session
from briar.journal.sinks.base import JournalSink
from briar.journal.store.base import JournalStore


log = logging.getLogger(__name__)


class Journal:
    """Coordinates `record` → store + sinks. Stateful across one
    session: `begin_session` returns the active `Session`, `end_session`
    seals it, persists, and publishes."""

    def __init__(self, store: JournalStore, sinks: Sequence[JournalSink] = ()) -> None:
        self._store = store
        self._sinks: List[JournalSink] = [s for s in sinks if s.is_available()]
        self._active: Optional[Session] = None

    @property
    def active(self) -> Optional[Session]:
        return self._active

    def begin_session(self, *, command: str, target: str = "") -> Session:
        if self._active is not None:
            raise RuntimeError(f"journal already has an active session {self._active.session_id}; nested sessions are not supported")
        self._active = Session(command=command, target=target)
        log.debug("journal session begin: id=%s command=%s target=%s", self._active.session_id, command, target)
        return self._active

    def record(self, event: DecisionEvent) -> None:
        if self._active is None:
            log.debug("journal record dropped — no active session: %s", event.choice)
            return
        self._active.record(event)

    def end_session(self) -> Optional[Session]:
        session = self._active
        if session is None:
            return None
        session.close()
        self._active = None
        try:
            self._store.put(session)
        except Exception:  # noqa: BLE001 — store failure logged, then re-raised so callers know
            log.exception("journal store put failed for session=%s", session.session_id)
            raise
        for sink in self._sinks:
            try:
                sink.publish(session)
            except Exception:  # noqa: BLE001 — sink failures are isolated per CLAUDE.md adapter discipline
                log.exception("journal sink %s publish failed for session=%s", sink.name, session.session_id)
        return session


class _NoOpJournal:
    """Module-level default. Lets instrumented call-sites unconditionally
    call `journal().record(...)` without guarding for "is journaling on."

    Single Responsibility: this class exists to satisfy the
    `Journal`-like contract with zero behaviour. It is the
    Null Object pattern, full stop."""

    active: Optional[Session] = None

    def begin_session(self, *, command: str, target: str = "") -> Session:  # noqa: D401, ARG002
        return Session(command=command, target=target)

    def record(self, event: DecisionEvent) -> None:  # noqa: ARG002
        return None

    def end_session(self) -> Optional[Session]:
        return None


_NOOP = _NoOpJournal()
_active_journal: object = _NOOP


def active_journal() -> Journal:
    """Return the currently-installed journal — or the no-op default
    when nobody has installed one. Call-sites use this so they don't
    need to know whether journaling is wired."""
    return _active_journal  # type: ignore[return-value]


def set_active_journal(journal: Optional[Journal]) -> None:
    """Install (or clear) the process-wide active journal. The CLI
    entrypoint installs one; tests install an in-memory journal;
    `None` restores the no-op default."""
    global _active_journal
    _active_journal = journal if journal is not None else _NOOP


@contextmanager
def session(*, command: str, target: str = "") -> Iterator[Session]:
    """Context-manager that bounds a session. Whatever code runs inside
    can call `record(...)` and the events accrue to the session. On
    exit (normal OR exception) the session is closed, persisted to the
    store, and fanned out to enabled sinks.

    Usage:
        with session(command="scaffold.implementation", target="acme"):
            record("source.kinds", value=args.source, rationale="...")
    """
    journal = active_journal()
    open_session = journal.begin_session(command=command, target=target)
    try:
        yield open_session
    finally:
        journal.end_session()


def record(
    choice: str,
    *,
    value: object,
    rationale: str = "",
    alternatives: Sequence[object] = (),
    artifacts: Optional[dict] = None,
    parent_event_id: str = "",
) -> None:
    """Convenience wrapper that builds a `DecisionEvent` and routes it
    through the active journal. Call-sites prefer this over building
    events by hand."""
    event = DecisionEvent(
        choice=choice,
        value=value,
        rationale=rationale,
        alternatives=tuple(alternatives),
        artifacts=dict(artifacts or {}),
        parent_event_id=parent_event_id,
    )
    active_journal().record(event)
