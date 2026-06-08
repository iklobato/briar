"""`briar plan status` — the journal-folding side of `collect_status`.

`collect_status` buckets plan cards by status (already covered in
tests/test_plan.py) AND folds per-card artifacts (commit sha, PR URL,
start time, rationales) out of every `plan.run` journal session targeting
the plan. `_journal_artifacts` is the uncovered branch-heavy half; these
tests drive it with a fake journal store whose decision events mirror the
orchestrator's `plan.run.card.*` conventions (see briar CLAUDE.md
"Orchestrator context conventions").
"""

from __future__ import annotations

from types import SimpleNamespace

from briar.plan._enums import PlanCardStatus
from briar.plan._models import ImplementationPlan, PlanCard
from briar.plan._status import collect_status, render_table


def _plan(*cards: PlanCard, name="demo") -> ImplementationPlan:
    return ImplementationPlan(name=name, board_url="jira:KAN", tracker="jira", project="KAN", cards=list(cards))


def _event(choice, value="", rationale="", artifacts=None, timestamp=""):
    return SimpleNamespace(choice=choice, value=value, rationale=rationale, artifacts=artifacts or {}, timestamp=timestamp)


class _FakeJournal:
    """Records `list`/`get` calls; returns canned sessions.

    `sessions` maps session_id → list of decision events. `refs` is the
    list returned from `list(command_prefix=...)`."""

    def __init__(self, refs, sessions, *, list_exc=None, get_exc=None) -> None:
        self._refs = refs
        self._sessions = sessions
        self._list_exc = list_exc
        self._get_exc = get_exc
        self.list_calls = []

    def list(self, *, command_prefix="", limit=50):
        self.list_calls.append(command_prefix)
        if self._list_exc:
            raise self._list_exc
        return self._refs

    def get(self, session_id):
        if self._get_exc:
            raise self._get_exc
        decisions = self._sessions.get(session_id)
        if decisions is None:
            return None
        return SimpleNamespace(decisions=decisions)


def _ref(session_id, target):
    return SimpleNamespace(session_id=session_id, target=target)


class TestArtifactFolding:
    def test_done_card_gets_commit_pr_and_rationale(self):
        plan = _plan(PlanCard(key="KAN-1", title="one", status=PlanCardStatus.DONE))
        journal = _FakeJournal(
            refs=[_ref("s1", "demo@acme/widgets")],
            sessions={
                "s1": [
                    _event("plan.run.card.start", value="KAN-1", timestamp="2026-01-01T00:00:00Z"),
                    _event(
                        "plan.run.card.completed",
                        value="KAN-1",
                        rationale="shipped",
                        artifacts={"commit": "abc1234def", "pr_url": "https://github.com/x/y/pull/9"},
                    ),
                ]
            },
        )
        snap = collect_status(plan, journal)
        assert journal.list_calls == ["plan.run"]
        done = snap["done"][0]
        assert done["key"] == "KAN-1"
        assert done["commit"] == "abc1234def"
        assert done["pr_url"] == "https://github.com/x/y/pull/9"
        assert done["rationale"] == "shipped"

    def test_in_progress_card_gets_started_at(self):
        plan = _plan(PlanCard(key="KAN-2", title="two", status=PlanCardStatus.IN_PROGRESS))
        journal = _FakeJournal(
            refs=[_ref("s1", "demo@acme/widgets")],
            sessions={"s1": [_event("plan.run.card.start", value="KAN-2", timestamp="2026-02-02T10:00:00Z")]},
        )
        snap = collect_status(plan, journal)
        assert snap["in_progress"][0]["started_at"] == "2026-02-02T10:00:00Z"

    def test_blocked_card_gets_failed_rationale(self):
        plan = _plan(PlanCard(key="KAN-3", title="three", status=PlanCardStatus.BLOCKED, last_attempt_summary="exploded"))
        journal = _FakeJournal(
            refs=[_ref("s1", "demo@acme/widgets")],
            sessions={"s1": [_event("plan.run.card.failed", value="KAN-3", rationale="missing creds")]},
        )
        snap = collect_status(plan, journal)
        blocked = snap["blocked"][0]
        assert blocked["last_attempt"] == "exploded"  # comes from the card, not journal
        assert blocked["rationale"] == "missing creds"

    def test_session_for_other_plan_is_ignored(self):
        # target prefix is `<plan.name>@`; a session for a different plan
        # must not contribute artifacts.
        plan = _plan(PlanCard(key="KAN-1", title="one", status=PlanCardStatus.DONE))
        journal = _FakeJournal(
            refs=[_ref("s1", "OTHER@acme/widgets")],
            sessions={"s1": [_event("plan.run.card.completed", value="KAN-1", artifacts={"commit": "ffffff"})]},
        )
        snap = collect_status(plan, journal)
        assert snap["done"][0]["commit"] == ""

    def test_started_at_keeps_first_start_event(self):
        # `setdefault` means the earliest start wins if a card is re-started.
        plan = _plan(PlanCard(key="KAN-2", title="two", status=PlanCardStatus.IN_PROGRESS))
        journal = _FakeJournal(
            refs=[_ref("s1", "demo@acme/widgets")],
            sessions={
                "s1": [
                    _event("plan.run.card.start", value="KAN-2", timestamp="FIRST"),
                    _event("plan.run.card.start", value="KAN-2", timestamp="SECOND"),
                ]
            },
        )
        snap = collect_status(plan, journal)
        assert snap["in_progress"][0]["started_at"] == "FIRST"

    def test_artifact_without_commit_or_pr_leaves_blank(self):
        plan = _plan(PlanCard(key="KAN-1", title="one", status=PlanCardStatus.DONE))
        journal = _FakeJournal(
            refs=[_ref("s1", "demo@acme/widgets")],
            sessions={"s1": [_event("plan.run.card.completed", value="KAN-1", artifacts={"commit": ""})]},
        )
        snap = collect_status(plan, journal)
        assert snap["done"][0]["commit"] == ""
        assert snap["done"][0]["pr_url"] == ""


class TestDegradesGracefully:
    def test_none_store_yields_blank_artifacts(self):
        plan = _plan(PlanCard(key="KAN-1", title="one", status=PlanCardStatus.DONE))
        snap = collect_status(plan, None)
        assert snap["done"][0]["commit"] == ""
        assert snap["counts"]["done"] == 1

    def test_list_raising_yields_blank(self):
        plan = _plan(PlanCard(key="KAN-1", title="one", status=PlanCardStatus.DONE))
        journal = _FakeJournal(refs=[], sessions={}, list_exc=RuntimeError("journal down"))
        snap = collect_status(plan, journal)
        assert snap["done"][0]["commit"] == ""

    def test_get_raising_skips_that_session(self):
        plan = _plan(PlanCard(key="KAN-1", title="one", status=PlanCardStatus.DONE))
        journal = _FakeJournal(refs=[_ref("s1", "demo@acme/widgets")], sessions={}, get_exc=RuntimeError("boom"))
        snap = collect_status(plan, journal)
        assert snap["done"][0]["commit"] == ""

    def test_none_session_skipped(self):
        plan = _plan(PlanCard(key="KAN-1", title="one", status=PlanCardStatus.DONE))
        # ref points at a session id that resolves to None.
        journal = _FakeJournal(refs=[_ref("missing", "demo@acme/widgets")], sessions={})
        snap = collect_status(plan, journal)
        assert snap["done"][0]["commit"] == ""


class TestRenderTableArtifacts:
    def test_done_row_shows_commit_and_pr(self):
        plan = _plan(PlanCard(key="KAN-1", title="one", status=PlanCardStatus.DONE))
        journal = _FakeJournal(
            refs=[_ref("s1", "demo@acme/widgets")],
            sessions={
                "s1": [
                    _event(
                        "plan.run.card.completed",
                        value="KAN-1",
                        artifacts={"commit": "abcdef1234567", "pr_url": "https://gh/pr/1"},
                    )
                ]
            },
        )
        text = render_table(collect_status(plan, journal))
        assert "commit abcdef123" in text  # truncated to 9 chars
        assert "PR https://gh/pr/1" in text

    def test_table_board_none_label(self):
        plan = ImplementationPlan(name="p", board_url="", tracker="", project="")
        text = render_table(collect_status(plan, None))
        assert "(board: (none))" in text
