"""Tests for `briar plan run` — the orchestrator loop.

External I/O (`agent implement`) is mocked via the
`briar.commands.agent.run_implement` seam — tests inject a controllable
stub so we exercise loop behavior (success, failure-stop,
continue-on-failure, limit, dry-run propagation) without touching git,
LLMs, or the network.
"""

from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest import mock

from briar.commands.plan import CommandPlan, RunOp
from briar.journal import (
    Journal,
    JournalRef,
    set_active_journal,
)
from briar.journal.store.file import FileJournalStore
from briar.plan import ImplementationPlan, PlanCard, save_plan
from briar.storage import make_store


def _seeded_plan(cards: List[PlanCard], *, name: str = "demo") -> ImplementationPlan:
    return ImplementationPlan(
        name=name,
        board_url="",
        tracker="github-issues",
        project="acme/widgets",
        cascade=False,
        cards=cards,
    )


def _run_args(name: str, root: Path, **overrides) -> argparse.Namespace:
    """Build the argparse.Namespace `RunOp.run` expects. Keep defaults
    aligned with `RunOp.add_arguments` so the test surface tracks the
    real CLI."""
    ns = argparse.Namespace()
    defaults = {
        "name": name,
        "limit": 0,
        "continue_on_failure": False,
        "company": "acme",
        "owner": "acme",
        "repo": "widgets",
        "tracker_project": "",
        "tracker": "github-issues",
        "provider": "github",
        "model": "",
        "max_iter": 0,
        "git_user_name": "",
        "git_user_email": "",
        "keep_worktree": False,
        "dry_run": False,
        "runbook": "",
        "knowledge": str(root),
        "meeting": "fireflies",
        "meeting_key": "",
        "meeting_query": "",
        "meeting_top_k": 3,
        "meeting_max_bytes": 50_000,
        "store": "file",
        "root": str(root),
        "format": "quiet",
        "verbose": False,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(ns, k, v)
    return ns


class PlanRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.kstore = make_store("file", file_root=self.tmp)
        # In-memory journal so the per-card decisions land somewhere
        # the tests can inspect.
        self.jstore = FileJournalStore(self.tmp / "_journal")
        set_active_journal(Journal(self.jstore, sinks=[]))

    def tearDown(self) -> None:
        set_active_journal(None)

    def _save(self, plan: ImplementationPlan) -> None:
        save_plan(self.kstore, plan)

    def test_empty_plan_completes_immediately(self) -> None:
        self._save(_seeded_plan([]))
        plan_cmd = CommandPlan()
        op = RunOp()
        with mock.patch("briar.commands.agent.run_implement") as impl:
            rc = op.run(plan_cmd, _run_args("demo", self.tmp))
        self.assertEqual(rc, 0)
        impl.assert_not_called()

    def test_all_cards_succeed_marks_all_done(self) -> None:
        plan = _seeded_plan(
            [
                PlanCard(key="A", title="a"),
                PlanCard(key="B", title="b", depends_on=["A"]),
            ]
        )
        self._save(plan)
        with mock.patch("briar.commands.agent.run_implement", return_value=0) as impl:
            rc = RunOp().run(CommandPlan(), _run_args("demo", self.tmp))
        self.assertEqual(rc, 0)
        self.assertEqual(impl.call_count, 2)
        # Reload to confirm persisted state.
        from briar.plan import load_plan

        reloaded = load_plan(self.kstore, "demo")
        self.assertEqual([c.status for c in reloaded.cards], ["done", "done"])

    def test_first_failure_stops_and_blocks_card(self) -> None:
        plan = _seeded_plan(
            [
                PlanCard(key="A", title="a"),
                PlanCard(key="B", title="b", depends_on=["A"]),
            ]
        )
        self._save(plan)
        with mock.patch("briar.commands.agent.run_implement", return_value=3) as impl:
            rc = RunOp().run(CommandPlan(), _run_args("demo", self.tmp))
        # Implement was called once (for A) before the stop.
        self.assertEqual(impl.call_count, 1)
        # rc returned is the implement rc, surfaced.
        self.assertEqual(rc, 3)
        from briar.plan import load_plan

        reloaded = load_plan(self.kstore, "demo")
        statuses = {c.key: c.status for c in reloaded.cards}
        self.assertEqual(statuses, {"A": "blocked", "B": "pending"})

    def test_continue_on_failure_keeps_going(self) -> None:
        plan = _seeded_plan(
            [
                PlanCard(key="A", title="a"),
                PlanCard(key="B", title="b"),
                PlanCard(key="C", title="c"),
            ]
        )
        self._save(plan)
        with mock.patch("briar.commands.agent.run_implement", side_effect=[4, 0, 0]) as impl:
            rc = RunOp().run(
                CommandPlan(),
                _run_args("demo", self.tmp, continue_on_failure=True),
            )
        self.assertEqual(impl.call_count, 3)
        # Final rc is 1 because at least one card was blocked.
        self.assertEqual(rc, 1)
        from briar.plan import load_plan

        reloaded = load_plan(self.kstore, "demo")
        statuses = {c.key: c.status for c in reloaded.cards}
        self.assertEqual(statuses, {"A": "blocked", "B": "done", "C": "done"})

    def test_limit_caps_iterations(self) -> None:
        plan = _seeded_plan(
            [
                PlanCard(key="A", title="a"),
                PlanCard(key="B", title="b"),
                PlanCard(key="C", title="c"),
            ]
        )
        self._save(plan)
        with mock.patch("briar.commands.agent.run_implement", return_value=0) as impl:
            rc = RunOp().run(CommandPlan(), _run_args("demo", self.tmp, limit=2))
        self.assertEqual(rc, 0)
        self.assertEqual(impl.call_count, 2)
        from briar.plan import load_plan

        reloaded = load_plan(self.kstore, "demo")
        statuses = {c.key: c.status for c in reloaded.cards}
        # First two cards done; third still pending because of the cap.
        self.assertEqual(statuses, {"A": "done", "B": "done", "C": "pending"})

    def test_dry_run_propagates_to_implement(self) -> None:
        self._save(_seeded_plan([PlanCard(key="A", title="a")]))
        captured: dict = {}

        def _capture(ns):
            captured["dry_run"] = ns.dry_run
            captured["ticket_key"] = ns.ticket_key
            captured["ticket_project"] = ns.ticket_project
            return 0

        with mock.patch("briar.commands.agent.run_implement", side_effect=_capture):
            RunOp().run(CommandPlan(), _run_args("demo", self.tmp, dry_run=True))
        self.assertTrue(captured["dry_run"])
        self.assertEqual(captured["ticket_key"], "A")
        # Default tracker_project = owner/repo.
        self.assertEqual(captured["ticket_project"], "acme/widgets")

    def test_tracker_project_override_is_passed_through(self) -> None:
        self._save(_seeded_plan([PlanCard(key="KAN-1", title="a")]))
        captured: dict = {}

        def _capture(ns):
            captured["ticket_project"] = ns.ticket_project
            return 0

        with mock.patch("briar.commands.agent.run_implement", side_effect=_capture):
            RunOp().run(
                CommandPlan(),
                _run_args("demo", self.tmp, tracker="jira", tracker_project="KAN"),
            )
        self.assertEqual(captured["ticket_project"], "KAN")

    def test_implement_exception_marks_card_blocked(self) -> None:
        self._save(_seeded_plan([PlanCard(key="A", title="a")]))
        with mock.patch("briar.commands.agent.run_implement", side_effect=RuntimeError("boom")):
            rc = RunOp().run(CommandPlan(), _run_args("demo", self.tmp))
        # Default stop-on-failure; rc surfaces non-zero.
        self.assertNotEqual(rc, 0)
        from briar.plan import load_plan

        reloaded = load_plan(self.kstore, "demo")
        self.assertEqual(reloaded.cards[0].status, "blocked")

    def test_journal_records_per_card_events(self) -> None:
        self._save(
            _seeded_plan(
                [
                    PlanCard(key="A", title="a"),
                    PlanCard(key="B", title="b"),
                ]
            )
        )
        with mock.patch("briar.commands.agent.run_implement", side_effect=[0, 3]):
            RunOp().run(CommandPlan(), _run_args("demo", self.tmp))
        sessions = self.jstore.list()
        self.assertEqual(len(sessions), 1)
        s = self.jstore.get(sessions[0].session_id)
        assert s is not None
        choices = [d.choice for d in s.decisions]
        # Loop wrote a start, two card.start events, one completed (A),
        # one failed (B), and a "stopped/first_failure" terminator.
        self.assertIn("plan.run.start", choices)
        self.assertEqual(choices.count("plan.run.card.start"), 2)
        self.assertIn("plan.run.card.completed", choices)
        self.assertIn("plan.run.card.failed", choices)
        self.assertIn("plan.run.stopped", choices)


if __name__ == "__main__":
    unittest.main()
