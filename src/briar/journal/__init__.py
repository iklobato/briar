"""briar.journal — record decisions, persist them, fan out to sinks.

Two concerns kept apart by the design (SRP):

- **Store** (`briar.journal.store`): pluggable system-of-record backends.
  One concrete today (`file`); postgres slots in identically.
- **Sinks** (`briar.journal.sinks`): pluggable publish destinations.
  One concrete today (`file` → markdown). Notion / Slack / Linear sinks
  add as sibling modules + one tuple entry each (Open/Closed).

Call-site surface is two functions and one context-manager:

    from briar.journal import session, record

    with session(command="scaffold.implementation", target="acme"):
        record("source.kinds", value=["github"], rationale="user passed --source")

If no journal is installed via `set_active_journal(...)`, the calls
are routed to a `_NoOpJournal` Null Object — instrumented code never
needs to check whether journaling is active."""

from __future__ import annotations

from briar.journal._journal import (
    Journal,
    active_journal,
    record,
    session,
    set_active_journal,
)
from briar.journal.models import DecisionEvent, Session
from briar.journal.sinks import JOURNAL_SINKS, JournalSink
from briar.journal.store import (
    JOURNAL_STORE_NAMES,
    JournalRef,
    JournalStore,
    JournalStoreBinding,
    make_journal_store,
)


__all__ = [
    "DecisionEvent",
    "JOURNAL_SINKS",
    "JOURNAL_STORE_NAMES",
    "Journal",
    "JournalRef",
    "JournalSink",
    "JournalStore",
    "JournalStoreBinding",
    "Session",
    "active_journal",
    "make_journal_store",
    "record",
    "session",
    "set_active_journal",
]
