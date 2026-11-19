"""WP1 of the production plan (P21, P32): while the plan limit is hit, the loop sends ONE task per poll to see whether the plan lets calls
through again (the first PR that needs the model, else the first resumed ticket, else the selector), and goes on with what needs no
model (a merged or closed PR). Any call that works opens the gate. One note a day in the report."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Tuple

import pytest

from briar.extract._provider import CommentKind, PullState, ReviewComment
from briar.serve.loop import CompanyWatch, TaskSlots, WatchWork
from briar.serve.select import Choice, Selector
from briar.serve.state import WorkEntry, WorkSource, WorkStatus
from briar.serve.work import ServeCompany, WorkDay, WorkKind
from tests.unit.serve.fakes import Companies, FakeGithub, FakeJira, FakeSources, Tickets
from tests.unit.serve.test_login_pause import Kinds, RefusedThenPicks
from tests.unit.serve.test_loop import KeyRoutine
from tests.unit.serve.test_select import PickingAgent

THREE_SLOTS = 3
THREE_POLLS = 3
PR = 41
MERGED_PR = 13
CLOSED_PR = 14
REASON = "the plan limit is hit: you have hit your limit"
NOTE = f"the plan limit is hit: one task is tried at each poll ({REASON})"


class Poll:
    """A company with a PR to review, two tickets of briar in progress, a merged and a closed PR."""

    def watch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: List[Tuple[str, WorkStatus, int]]) -> Tuple[ServeCompany, KeyRoutine, CompanyWatch, TaskSlots]:
        review = ReviewComment(id="1", author="ann", body="rename this", kind=CommentKind.INLINE, association="MEMBER", updated_at="t1")
        pulls = {("acme/app", MERGED_PR): PullState(False, True, False, "main"), ("acme/app", CLOSED_PR): PullState(False, False, False, "main")}
        github = FakeGithub(comments={("acme/app", PR): [review]}, pulls=pulls)
        tickets = [Tickets().jira(key, "High", "2026-01-03") for key in ("ENG-7", "ENG-4")]
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources(github, jira_adapter=FakeJira({"ENG": tickets})))
        company = Companies(tmp_path / "knowledge").company({"jira": {"ENG": {"repos": ["acme/app"]}}})
        company.gate.hit(REASON)
        for key, status, pr in rows:
            asyncio.run(company.state.record(WorkEntry(key=key, source=WorkSource.JIRA, repo="acme/app", title=key, status=status, pr=pr)))
        routine, slots = KeyRoutine(), TaskSlots(asyncio.Semaphore(THREE_SLOTS), set(), set())
        return company, routine, CompanyWatch(company, slots, WatchWork(routine, Choice(Selector(PickingAgent([]), company), company))), slots

    def polls(self, watch: CompanyWatch, slots: TaskSlots, count: int) -> None:
        async def scenario() -> None:
            for _ in range(count):
                await watch.cycle()
                await slots.drain()

        asyncio.run(scenario())


class Rows:
    def everything(self) -> List[Tuple[str, WorkStatus, int]]:
        return [
            ("ENG-8", WorkStatus.DONE, PR),
            ("ENG-7", WorkStatus.PLANNED, 0),
            ("ENG-6", WorkStatus.DONE, MERGED_PR),
            ("ENG-5", WorkStatus.DONE, CLOSED_PR),
        ]

    def resumed_only(self) -> List[Tuple[str, WorkStatus, int]]:
        return [("ENG-7", WorkStatus.PLANNED, 0), ("ENG-4", WorkStatus.FOUND, 0)]


class TestClosedGate:
    def test_one_task_goes_through_and_what_needs_no_model_goes_on(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, routine, watch, slots = Poll().watch(tmp_path, monkeypatch, Rows().everything())

        Poll().polls(watch, slots, 1)

        expected = [(WorkKind.REVIEW, "ENG-8"), (WorkKind.MERGED, "ENG-6"), (WorkKind.CLOSED, "ENG-5")]
        assert sorted(routine.dispatched, key=str) == sorted(expected, key=str)

    def test_with_only_resumed_tickets_exactly_one_is_tried(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, routine, watch, slots = Poll().watch(tmp_path, monkeypatch, Rows().resumed_only())

        Poll().polls(watch, slots, 1)

        assert len(routine.dispatched) == 1

    def test_many_polls_leave_one_note_for_the_day(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        company, _, watch, slots = Poll().watch(tmp_path, monkeypatch, Rows().everything())

        Poll().polls(watch, slots, THREE_POLLS)

        assert company.notes[WorkDay().today()] == [NOTE]


class TestOpenGate:
    def test_with_the_gate_open_everything_goes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        company, routine, watch, slots = Poll().watch(tmp_path, monkeypatch, Rows().everything())
        company.gate.clear()

        Poll().polls(watch, slots, 1)

        expected = [(WorkKind.REVIEW, "ENG-8"), (WorkKind.TICKET, "ENG-7"), (WorkKind.MERGED, "ENG-6"), (WorkKind.CLOSED, "ENG-5")]
        assert sorted(routine.dispatched, key=str) == sorted(expected, key=str)


class TestTheSelectorIsTheProbe:
    """With nothing else to try, the selector call asks the plan: a refusal keeps the gate closed, an answer opens it."""

    def watch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: object) -> Tuple[ServeCompany, KeyRoutine, CompanyWatch, TaskSlots]:
        jira = FakeJira({"ENG": [Tickets().jira("ENG-1", "High", "2026-01-03")]})
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources(jira_adapter=jira))
        company = Companies(tmp_path / "knowledge").company({"jira": {"ENG": {"repos": ["acme/app", "acme/api"]}}})
        company.gate.hit(REASON)
        routine, slots = KeyRoutine(), TaskSlots(asyncio.Semaphore(THREE_SLOTS), set(), set())
        return company, routine, CompanyWatch(company, slots, WatchWork(routine, Choice(Selector(agent, company), company))), slots  # type: ignore[arg-type]

    def test_a_refused_selector_starts_nothing_and_the_gate_stays_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = RefusedThenPicks(Kinds().limit())
        company, routine, watch, slots = self.watch(tmp_path, monkeypatch, agent)

        Poll().polls(watch, slots, 1)

        assert (routine.dispatched, company.gate.closed(), len(agent.requests)) == ([], True, 1)

    def test_a_selector_that_answers_opens_the_gate_and_the_pick_starts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = PickingAgent([{"key": "ENG-1", "repo": "acme/app", "reason": "the only ticket"}])
        company, routine, watch, slots = self.watch(tmp_path, monkeypatch, agent)

        Poll().polls(watch, slots, 1)

        assert (routine.dispatched, company.gate.closed()) == ([(WorkKind.TICKET, "ENG-1")], False)
