"""Journal façade — store-as-system-of-record + sink fan-out resilience."""

from __future__ import annotations

import pytest

from briar.journal import Journal, record, session
from briar.journal._journal import (
    _NoOpJournal,
    active_journal,
    set_active_journal,
)
from briar.journal.models import DecisionEvent, Session


class _MemoryStore:
    def __init__(self):
        self.puts: list[Session] = []
        self.fail = False

    def put(self, session: Session) -> None:
        if self.fail:
            raise RuntimeError("store down")
        self.puts.append(session)


class _MemorySink:
    name = "memory"

    def __init__(self, available: bool = True, fail: bool = False):
        self._available = available
        self.fail = fail
        self.published: list[Session] = []

    def is_available(self) -> bool:
        return self._available

    def publish(self, session: Session) -> bool:
        if self.fail:
            raise RuntimeError("sink down")
        self.published.append(session)
        return True


@pytest.fixture(autouse=True)
def restore_journal():
    yield
    set_active_journal(None)


class TestSessionLifecycle:
    def test_begin_then_end_persists_to_store(self) -> None:
        store = _MemoryStore()
        j = Journal(store, sinks=[])
        j.begin_session(command="test.cmd")
        j.end_session()
        assert len(store.puts) == 1
        assert store.puts[0].command == "test.cmd"

    def test_nested_session_raises(self) -> None:
        j = Journal(_MemoryStore(), sinks=[])
        j.begin_session(command="a")
        with pytest.raises(RuntimeError, match="nested"):
            j.begin_session(command="b")

    def test_record_outside_session_dropped(self, caplog_briar) -> None:
        j = Journal(_MemoryStore(), sinks=[])
        # No session open
        j.record(DecisionEvent(choice="x", value=1))
        # No exception; record dropped with debug log
        assert any("dropped" in r.message for r in caplog_briar.records)

    def test_end_without_session_noop(self) -> None:
        j = Journal(_MemoryStore(), sinks=[])
        assert j.end_session() is None


class TestStoreFailure:
    def test_store_put_failure_propagates(self) -> None:
        store = _MemoryStore()
        store.fail = True
        j = Journal(store, sinks=[])
        j.begin_session(command="x")
        with pytest.raises(RuntimeError, match="store down"):
            j.end_session()


class TestSinkResilience:
    def test_sink_failure_logged_does_not_abort(self, caplog_briar) -> None:
        store = _MemoryStore()
        failing = _MemorySink(fail=True)
        ok = _MemorySink()
        j = Journal(store, sinks=[failing, ok])
        j.begin_session(command="x")
        j.end_session()
        # Both sinks attempted; store still happy.
        assert len(store.puts) == 1
        assert len(ok.published) == 1

    def test_unavailable_sink_filtered_at_construction(self) -> None:
        j = Journal(_MemoryStore(), sinks=[_MemorySink(available=False)])
        # The unavailable sink is dropped at __init__.
        assert j._sinks == []


class TestNoOpDefault:
    def test_active_journal_default_noop(self) -> None:
        set_active_journal(None)
        assert isinstance(active_journal(), _NoOpJournal)

    def test_record_via_noop_does_not_raise(self) -> None:
        set_active_journal(None)
        # No session open under noop; record() should be silent.
        record("test.event", value=1)


class TestContextManager:
    def test_session_context_closes_on_normal_exit(self) -> None:
        store = _MemoryStore()
        set_active_journal(Journal(store, sinks=[]))
        with session(command="cm.test"):
            record("step", value=1)
        assert len(store.puts) == 1
        assert store.puts[0].command == "cm.test"

    def test_session_context_closes_on_exception(self) -> None:
        store = _MemoryStore()
        set_active_journal(Journal(store, sinks=[]))
        with pytest.raises(RuntimeError):
            with session(command="cm.fail"):
                raise RuntimeError("inside")
        # Session was closed and persisted despite the exception
        assert len(store.puts) == 1
