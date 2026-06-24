"""Tests for `briar plan run` — the LLM-driven orchestrator loop.

External I/O (`agent implement`, the LLM provider) is mocked. A
deterministic `FakeLLM` returns canned JSON responses so the loop's
selector / writeback / replan branches can be exercised offline.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest import mock

from briar.agent._llm import LLMResponse
from briar.commands.plan import CommandPlan, RunOp
from briar.journal import Journal, set_active_journal
from briar.journal.store.file import FileJournalStore
from briar.plan import ImplementationPlan, PlanCard, save_plan
from briar.storage import make_store


def _seeded_plan(cards: List[PlanCard], *, name: str = "demo") -> ImplementationPlan:
    return ImplementationPlan(
        name=name,
        board_url="",
        tracker="github-issues",
        project="acme/widgets",
        company="acme",
        cards=cards,
    )


def _run_args(name: str, root: Path, *, journal_root: Path, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace()
    defaults = {
        "name": name,
        "limit": 0,
        "continue_on_failure": False,
        "max_replans": 3,
        "company": "acme",
        "owner": "acme",
        "repo": "widgets",
        "tracker_project": "",
        "tracker": "github-issues",
        "provider": "github",
        "llm": "anthropic",
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
        "chat": "slack",
        "slack_query": "",
        "slack_top_k": 3,
        "slack_max_bytes": 30_000,
        "store": "file",
        "root": str(root),
        "journal_store": "file",
        "journal_root": str(journal_root),
        "format": "quiet",
        "verbose": False,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(ns, k, v)
    return ns


class _FakeLLM:
    """Canned-response LLMProvider for run-loop tests."""

    kind = "fake"

    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0

    def is_available(self) -> bool:
        return True

    def complete(self, *, system, messages, tools, max_tokens):
        if not self._responses:
            raise AssertionError(f"FakeLLM exhausted after {self.call_count} calls")
        text = self._responses.pop(0)
        self.call_count += 1
        return LLMResponse(text=text, tool_calls=[], stop_reason="end_turn", input_tokens=0, output_tokens=0)

    def format_tool_result(self, tool_call_id, output, is_error=False):
        return {}


def _pick(key: str) -> str:
    return json.dumps({"action": "pick", "key": key, "why": "next"})


def _complete() -> str:
    return json.dumps({"action": "complete", "why": "done"})


def _blocked() -> str:
    return json.dumps({"action": "blocked", "why": "stuck"})


def _replan() -> str:
    return json.dumps({"action": "replan", "why": "drift"})


def _wb(body: str = "updated") -> str:
    return json.dumps({"body": body})


class PlanRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.kstore = make_store("file", file_root=self.tmp)
        self.journal_root = self.tmp / "_journal"
        self.jstore = FileJournalStore(self.journal_root)
        set_active_journal(Journal(self.jstore, sinks=[]))

    def tearDown(self) -> None:
        set_active_journal(None)

    def _save(self, plan: ImplementationPlan) -> None:
        save_plan(self.kstore, plan)

    def _run(self, llm: _FakeLLM, args: argparse.Namespace, *, impl_return=0, impl_side=None):
        # Patch `make_llm` (used for both selector + writeback) to return the same FakeLLM.
        with mock.patch("briar.commands.plan.make_llm", return_value=llm):
            kw = {"side_effect": impl_side} if impl_side is not None else {"return_value": impl_return}
            with mock.patch("briar.commands.agent.run_implement", **kw) as impl:
                rc = RunOp().run(CommandPlan(), args)
        return rc, impl

    def test_empty_plan_completes_without_llm(self) -> None:
        """Empty plan → selector short-circuits to COMPLETE (no LLM call)."""
        self._save(_seeded_plan([]))
        llm = _FakeLLM([])
        args = _run_args("demo", self.tmp, journal_root=self.journal_root)
        rc, impl = self._run(llm, args)
        self.assertEqual(rc, 0)
        impl.assert_not_called()
        self.assertEqual(llm.call_count, 0)

    def test_repo_slug_splits_into_owner_and_repo(self) -> None:
        # `--repo owner/repo` (no --owner) is normalised in place.
        self._save(_seeded_plan([]))
        llm = _FakeLLM([])
        args = _run_args("demo", self.tmp, journal_root=self.journal_root, owner="", repo="acme/widgets")
        rc, _ = self._run(llm, args)
        self.assertEqual(rc, 0)
        self.assertEqual((args.owner, args.repo), ("acme", "widgets"))

    def test_missing_repo_target_raises(self) -> None:
        from briar.errors import CliError

        self._save(_seeded_plan([]))
        args = _run_args("demo", self.tmp, journal_root=self.journal_root, owner="", repo="")
        with self.assertRaises(CliError):
            self._run(_FakeLLM([]), args)

    def test_all_cards_succeed_marks_all_done(self) -> None:
        plan = _seeded_plan([PlanCard(key="A", title="a"), PlanCard(key="B", title="b")])
        self._save(plan)
        # pick A, writeback, pick B, writeback, complete
        llm = _FakeLLM([_pick("A"), _wb(), _pick("B"), _wb(), _complete()])
        args = _run_args("demo", self.tmp, journal_root=self.journal_root)
        rc, impl = self._run(llm, args)
        self.assertEqual(rc, 0)
        self.assertEqual(impl.call_count, 2)
        from briar.plan import load_plan

        reloaded = load_plan(self.kstore, "demo")
        self.assertEqual([c.status.value for c in reloaded.cards], ["done", "done"])

    def test_first_failure_stops_and_blocks_card(self) -> None:
        plan = _seeded_plan([PlanCard(key="A", title="a"), PlanCard(key="B", title="b")])
        self._save(plan)
        llm = _FakeLLM([_pick("A")])  # no writeback (rc!=0), no further picks
        args = _run_args("demo", self.tmp, journal_root=self.journal_root)
        rc, impl = self._run(llm, args, impl_return=3)
        self.assertEqual(impl.call_count, 1)
        self.assertEqual(rc, 3)
        from briar.plan import load_plan

        reloaded = load_plan(self.kstore, "demo")
        statuses = {c.key: c.status.value for c in reloaded.cards}
        self.assertEqual(statuses, {"A": "blocked", "B": "pending"})
        # The blocked card carries the failure summary.
        blocked = next(c for c in reloaded.cards if c.key == "A")
        self.assertIn("implement rc=3", blocked.last_attempt_summary)

    def test_continue_on_failure_keeps_going(self) -> None:
        plan = _seeded_plan([PlanCard(key="A", title="a"), PlanCard(key="B", title="b"), PlanCard(key="C", title="c")])
        self._save(plan)
        # A fails (no writeback), B & C succeed (each with writeback), then complete.
        llm = _FakeLLM([_pick("A"), _pick("B"), _wb(), _pick("C"), _wb(), _complete()])
        args = _run_args("demo", self.tmp, journal_root=self.journal_root, continue_on_failure=True)
        rc, impl = self._run(llm, args, impl_side=[4, 0, 0])
        self.assertEqual(impl.call_count, 3)
        # Final rc is 1 because at least one card was blocked.
        self.assertEqual(rc, 1)
        from briar.plan import load_plan

        reloaded = load_plan(self.kstore, "demo")
        statuses = {c.key: c.status.value for c in reloaded.cards}
        self.assertEqual(statuses, {"A": "blocked", "B": "done", "C": "done"})

    def test_limit_caps_iterations(self) -> None:
        plan = _seeded_plan([PlanCard(key="A", title="a"), PlanCard(key="B", title="b"), PlanCard(key="C", title="c")])
        self._save(plan)
        llm = _FakeLLM([_pick("A"), _wb(), _pick("B"), _wb()])  # limit=2 → stops before C
        args = _run_args("demo", self.tmp, journal_root=self.journal_root, limit=2)
        rc, impl = self._run(llm, args)
        self.assertEqual(rc, 0)
        self.assertEqual(impl.call_count, 2)
        from briar.plan import load_plan

        reloaded = load_plan(self.kstore, "demo")
        statuses = {c.key: c.status.value for c in reloaded.cards}
        self.assertEqual(statuses, {"A": "done", "B": "done", "C": "pending"})

    def test_dry_run_propagates_to_implement(self) -> None:
        self._save(_seeded_plan([PlanCard(key="A", title="a")]))
        llm = _FakeLLM([_pick("A"), _wb(), _complete()])
        captured: dict = {}

        def _capture(ns):
            captured["dry_run"] = ns.dry_run
            captured["ticket_key"] = ns.ticket_key
            captured["ticket_project"] = ns.ticket_project
            return 0

        args = _run_args("demo", self.tmp, journal_root=self.journal_root, dry_run=True)
        with mock.patch("briar.commands.plan.make_llm", return_value=llm):
            with mock.patch("briar.commands.agent.run_implement", side_effect=_capture):
                RunOp().run(CommandPlan(), args)
        self.assertTrue(captured["dry_run"])
        self.assertEqual(captured["ticket_key"], "A")
        self.assertEqual(captured["ticket_project"], "acme/widgets")

    def test_blocked_action_stops_loop(self) -> None:
        self._save(_seeded_plan([PlanCard(key="A", title="a")]))
        llm = _FakeLLM([_blocked()])
        args = _run_args("demo", self.tmp, journal_root=self.journal_root)
        rc, impl = self._run(llm, args)
        self.assertNotEqual(rc, 0)
        impl.assert_not_called()

    def test_replan_action_rebuilds_plan(self) -> None:
        self._save(_seeded_plan([PlanCard(key="A", title="a")]))
        llm = _FakeLLM([_replan(), _pick("A"), _wb(), _complete()])
        args = _run_args("demo", self.tmp, journal_root=self.journal_root)

        def _fake_replan(old, **kw):
            # Identity replan — same plan back.
            return old

        with mock.patch("briar.commands.plan.replan", side_effect=_fake_replan) as rp:
            rc, impl = self._run(llm, args)
        self.assertEqual(rc, 0)
        self.assertEqual(rp.call_count, 1)
        self.assertEqual(impl.call_count, 1)

    def test_replan_cap_stops_loop(self) -> None:
        self._save(_seeded_plan([PlanCard(key="A", title="a")]))
        llm = _FakeLLM([_replan(), _replan()])
        args = _run_args("demo", self.tmp, journal_root=self.journal_root, max_replans=1)
        with mock.patch("briar.commands.plan.replan", side_effect=lambda old, **kw: old):
            rc, impl = self._run(llm, args)
        self.assertNotEqual(rc, 0)
        impl.assert_not_called()

    def test_implement_exception_marks_card_blocked(self) -> None:
        self._save(_seeded_plan([PlanCard(key="A", title="a")]))
        llm = _FakeLLM([_pick("A")])
        args = _run_args("demo", self.tmp, journal_root=self.journal_root)
        with mock.patch("briar.commands.plan.make_llm", return_value=llm):
            with mock.patch("briar.commands.agent.run_implement", side_effect=RuntimeError("boom")):
                rc = RunOp().run(CommandPlan(), args)
        self.assertNotEqual(rc, 0)
        from briar.plan import load_plan

        reloaded = load_plan(self.kstore, "demo")
        self.assertEqual(reloaded.cards[0].status.value, "blocked")
        self.assertIn("RuntimeError", reloaded.cards[0].last_attempt_summary)

    def test_writeback_called_on_success(self) -> None:
        """Writeback runs after rc=0 and the result lands in `knowledge:<company>.<plan>`."""
        self._save(_seeded_plan([PlanCard(key="A", title="a")]))
        new_body = "## new plan knowledge\n- A is done"
        llm = _FakeLLM([_pick("A"), _wb(new_body), _complete()])
        args = _run_args("demo", self.tmp, journal_root=self.journal_root)
        rc, _ = self._run(llm, args)
        self.assertEqual(rc, 0)
        self.assertEqual(self.kstore.get("knowledge:acme.demo"), new_body)

    def test_journal_records_per_card_events(self) -> None:
        self._save(_seeded_plan([PlanCard(key="A", title="a"), PlanCard(key="B", title="b")]))
        # A succeeds (writeback), B fails, loop stops on first failure.
        llm = _FakeLLM([_pick("A"), _wb(), _pick("B")])
        args = _run_args("demo", self.tmp, journal_root=self.journal_root)
        self._run(llm, args, impl_side=[0, 3])
        sessions = self.jstore.list()
        self.assertEqual(len(sessions), 1)
        s = self.jstore.get(sessions[0].session_id)
        assert s is not None
        choices = [d.choice for d in s.decisions]
        self.assertIn("plan.run.start", choices)
        self.assertIn("plan.next.decision", choices)
        self.assertIn("plan.run.card.start", choices)
        self.assertIn("plan.run.card.completed", choices)
        self.assertIn("plan.run.card.failed", choices)
        self.assertIn("plan.run.stopped", choices)


if __name__ == "__main__":
    unittest.main()
