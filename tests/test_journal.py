"""Tests for the journal package.

Covers the four seams:
- Value objects (DecisionEvent / Session) — invariants
- Store (FileJournalStore) — put/get/list roundtrip
- Sink (FileSink) — markdown publish
- Journal façade — record routing, store-on-close, sink fan-out,
  null-object default when no journal is installed

Plus one integration test: the scaffold composer, run end-to-end with
an installed journal, records the expected decisions.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from briar.iac import TEMPLATES
from briar.journal import (
    DecisionEvent,
    Journal,
    JOURNAL_STORE_NAMES,
    Session,
    active_journal,
    make_journal_store,
    record,
    session,
    set_active_journal,
)
from briar.journal.sinks.file import FileSink
from briar.journal.store.file import FileJournalStore


def _ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "owner": "iklobato",
        "repo": "lightapi",
        "prefix": "test",
        "source": ["github"],
        "archetype": "engineer",
        "shape": "plan-approve-act",
        "trigger_kind": "github_webhook",
        "llm_provider_key": "anthropic",
        "model": "claude-sonnet-4-6",
        "auth_mode": "oauth",
        "github_secret_id": None,
        "jira_project": [],
        "jira_jql": None,
        "jira_secret_id": None,
        "aws_role_arn": None,
        "aws_external_id": None,
        "aws_region": "us-east-1",
        "aws_services": [],
        "webhook_events": [],
        "webhook_labels": ["briar"],
        "bitbucket_workspace": None,
        "bitbucket_repo": None,
        "bitbucket_secret_id": None,
        "bitbucket_authors_allow": [],
        "bitbucket_authors_block": [],
        "bitbucket_assignees_allow": [],
        "bitbucket_assignees_block": [],
        "bitbucket_webhook_events": [],
        "bitbucket_webhook_labels": ["briar"],
        "sentry_org": None,
        "sentry_project": [],
        "sentry_environment": [],
        "sentry_query": None,
        "sentry_level": [],
        "sentry_secret_id": None,
        "schedule": "0 * * * *",
    }
    defaults.update(kwargs)
    ns = argparse.Namespace()
    for k, v in defaults.items():
        setattr(ns, k, v)
    return ns


class SessionLifecycleTests(unittest.TestCase):
    def test_open_session_accepts_records(self) -> None:
        s = Session(command="x")
        s.record(DecisionEvent(choice="a", value=1))
        s.record(DecisionEvent(choice="b", value=2))
        self.assertEqual(len(s.decisions), 2)
        self.assertFalse(s.closed)

    def test_closed_session_rejects_records(self) -> None:
        s = Session(command="x")
        s.close()
        self.assertTrue(s.closed)
        self.assertNotEqual(s.ended_at, "")
        with self.assertRaises(RuntimeError):
            s.record(DecisionEvent(choice="a", value=1))

    def test_session_roundtrip_through_dict(self) -> None:
        s = Session(command="x", target="acme", metadata={"k": "v"})
        s.record(DecisionEvent(choice="a", value=[1, 2], rationale="because"))
        s.close()
        round_tripped = Session.from_dict(s.to_dict())
        self.assertEqual(round_tripped.command, "x")
        self.assertEqual(round_tripped.target, "acme")
        self.assertEqual(round_tripped.metadata["k"], "v")
        self.assertEqual(round_tripped.decisions[0].choice, "a")
        self.assertEqual(round_tripped.decisions[0].value, [1, 2])
        self.assertEqual(round_tripped.decisions[0].rationale, "because")


class FileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_put_get_roundtrip(self) -> None:
        store = FileJournalStore(self.tmp)
        s = Session(command="cmd", target="acme")
        s.record(DecisionEvent(choice="a", value=1))
        s.close()
        ref = store.put(s)
        self.assertEqual(ref.session_id, s.session_id)
        self.assertEqual(ref.decision_count, 1)
        retrieved = store.get(s.session_id)
        self.assertIsNotNone(retrieved)
        assert retrieved is not None
        self.assertEqual(retrieved.session_id, s.session_id)
        self.assertEqual(retrieved.decisions[0].choice, "a")

    def test_put_refuses_open_session(self) -> None:
        store = FileJournalStore(self.tmp)
        s = Session(command="cmd")
        with self.assertRaises(RuntimeError):
            store.put(s)

    def test_get_missing_returns_none(self) -> None:
        store = FileJournalStore(self.tmp)
        self.assertIsNone(store.get("nope"))

    def test_list_filters_by_command_prefix(self) -> None:
        store = FileJournalStore(self.tmp)
        for command in ("scaffold.implementation", "scaffold.pr-fixes", "extract.run"):
            s = Session(command=command)
            s.close()
            store.put(s)
        refs = store.list(command_prefix="scaffold.")
        self.assertEqual(len(refs), 2)
        for ref in refs:
            self.assertTrue(ref.command.startswith("scaffold."))

    def test_registry_builds_file_store(self) -> None:
        store = make_journal_store("file", file_root=self.tmp)
        self.assertIsInstance(store, FileJournalStore)
        self.assertIn("file", JOURNAL_STORE_NAMES)


class FileSinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_publishes_markdown(self) -> None:
        sink = FileSink(root=self.tmp)
        self.assertTrue(sink.is_available())
        s = Session(command="cmd", target="acme")
        s.record(DecisionEvent(choice="a.b", value="v", rationale="why"))
        s.close()
        self.assertTrue(sink.publish(s))
        path = self.tmp / "published" / f"{s.session_id}.md"
        self.assertTrue(path.exists())
        content = path.read_text()
        self.assertIn("cmd — acme", content)
        self.assertIn("a.b", content)
        self.assertIn("why", content)

    def test_publish_refuses_open_session(self) -> None:
        sink = FileSink(root=self.tmp)
        # The decorator swallows the RuntimeError + returns False — the
        # caller's resilience contract is to log and continue.
        self.assertFalse(sink.publish(Session(command="cmd")))


class JournalFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = FileJournalStore(self.tmp)
        self.sink = FileSink(root=self.tmp)
        self.journal = Journal(self.store, sinks=[self.sink])

    def tearDown(self) -> None:
        set_active_journal(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_begin_record_end_persists_and_publishes(self) -> None:
        s = self.journal.begin_session(command="cmd", target="acme")
        self.journal.record(DecisionEvent(choice="x", value=1))
        ended = self.journal.end_session()
        self.assertIsNotNone(ended)
        assert ended is not None
        self.assertTrue(ended.closed)
        # Persisted to store
        self.assertIsNotNone(self.store.get(s.session_id))
        # Published to sink (file present)
        self.assertTrue((self.tmp / "published" / f"{s.session_id}.md").exists())

    def test_nested_session_rejected(self) -> None:
        self.journal.begin_session(command="cmd")
        with self.assertRaises(RuntimeError):
            self.journal.begin_session(command="cmd2")

    def test_no_active_journal_is_noop(self) -> None:
        # set_active_journal(None) restores the null-object default.
        set_active_journal(None)
        # These should not raise even though no journal is installed:
        record("a", value=1, rationale="r")
        with session(command="cmd"):
            record("b", value=2)
        # Nothing was persisted:
        self.assertEqual(self.store.list(), [])

    def test_session_context_manager_persists(self) -> None:
        set_active_journal(self.journal)
        with session(command="cmd", target="t"):
            record("a", value=1, rationale="r", alternatives=("x", "y"))
        refs = self.store.list()
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].command, "cmd")
        self.assertEqual(refs[0].decision_count, 1)


class ComposerInstrumentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = FileJournalStore(self.tmp)
        self.journal = Journal(self.store, sinks=[])
        set_active_journal(self.journal)

    def tearDown(self) -> None:
        set_active_journal(None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scaffold_implementation_records_four_resolutions(self) -> None:
        tmpl = TEMPLATES["implementation"]
        with session(command="scaffold.implementation", target="test"):
            tmpl.build(_ns())
        refs = self.store.list()
        self.assertEqual(len(refs), 1)
        s = self.store.get(refs[0].session_id)
        assert s is not None
        choices = [d.choice for d in s.decisions]
        self.assertIn("scaffold.sources", choices)
        self.assertIn("scaffold.archetype", choices)
        self.assertIn("scaffold.shape", choices)
        self.assertIn("scaffold.trigger", choices)
        self.assertIn("scaffold.tools.filtered", choices)


if __name__ == "__main__":
    unittest.main()
