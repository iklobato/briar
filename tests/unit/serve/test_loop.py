"""The loop of `briar serve` (spec 003 PR 1: S15, S16, S17): parallel tasks with a limit,
never two on the same ticket, and the state they write. No network: the finder reads
the fakes of `fakes.py`."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

import pytest

from briar.auth.check import SourceUnreachable
from briar.extract._provider import CommentKind, PullState, ReviewComment
from briar.extract._tracker import Ticket
from briar.serve.loop import CompanyWatch, TaskSlots, WatchWork
from briar.serve.select import Choice, Selector
from briar.serve.state import WorkState
from briar.serve.state import MoneyStop, WorkEntry, WorkSource, WorkStatus
from briar.serve.work import ServeCompany, WorkDay, WorkItem, WorkKind
from tests.unit.serve.fakes import Companies, FakeGithub, FakeJira, FakeSources, Tickets
from tests.unit.serve.test_select import PickingAgent

TWO_SLOTS = 2
THREE_SLOTS = 3
THREE_POLLS = 3
# The merged PR of `TestDayGate`.
MERGED_PR = 13
# The US$ a day cap of `TestDailyMoneyLimit`.
DAY_CAP = 10.0
# What the gate says once for the day when the booked spend is at the cap.
GATE_NOTE = "the daily cap max_usd_per_day of US$ 10 is reached: nothing that needs the model runs until tomorrow (UTC)"
# A big spend on another day: it must not count against today.
OTHER_DAY_SPEND = 100.0
FIVE_TASKS = 5
# A ticket that stopped for money at `max_usd` 5 after spending 5.1, and the `max_usd` after a raise.
CAP_AT_STOP = 5.0
SPENT_AT_STOP = 5.1
RAISED_USD = 8.0
PR = 41


@dataclass(frozen=True)
class CountingRoutine:
    """Holds each task until `release` is set, and counts how many run at once."""

    release: asyncio.Event
    started: List[str] = field(default_factory=list)
    most_at_once: List[int] = field(default_factory=lambda: [0])
    running: List[int] = field(default_factory=lambda: [0])

    async def work_on(self, item: WorkItem) -> None:
        self.started.append(item.key)
        self.running[0] += 1
        self.most_at_once[0] = max(self.most_at_once[0], self.running[0])
        await self.release.wait()
        self.running[0] -= 1


class TaskBroke(Exception):
    """What a broken routine raises."""


class FailingRoutine:
    async def work_on(self, item: WorkItem) -> None:
        raise TaskBroke(f"broke on {item.key}")


class RecordingRoutine:
    """Records each ticket as taken (`pr-open`), as the ticket routine does when it opens the PR."""

    def __init__(self, state: WorkState) -> None:
        self._state = state

    async def work_on(self, item: WorkItem) -> None:
        await self._state.record(item.entry().model_copy(update={"status": WorkStatus.PR_OPEN}))


class Items:
    def ticket(self, key: str) -> WorkItem:
        return WorkItem(WorkKind.TICKET, key, WorkSource.JIRA, "acme/app", f"title of {key}")


class TestTaskSlots:
    def test_at_most_max_parallel_tasks_run_at_once(self) -> None:
        async def scenario() -> CountingRoutine:
            routine = CountingRoutine(asyncio.Event())
            slots = TaskSlots(asyncio.Semaphore(TWO_SLOTS), set(), set())
            for number in range(FIVE_TASKS):
                slots.dispatch(Items().ticket(f"ENG-{number}"), routine)
            await asyncio.sleep(0)
            routine.release.set()
            await slots.drain()
            return routine

        routine = asyncio.run(scenario())

        assert (routine.most_at_once[0], len(routine.started)) == (TWO_SLOTS, FIVE_TASKS)

    def test_a_ticket_already_running_is_not_started_again(self) -> None:
        async def scenario() -> List[bool]:
            routine = CountingRoutine(asyncio.Event())
            slots = TaskSlots(asyncio.Semaphore(TWO_SLOTS), set(), set())
            dispatched = [slots.dispatch(Items().ticket("ENG-1"), routine), slots.dispatch(Items().ticket("ENG-1"), routine)]
            routine.release.set()
            await slots.drain()
            return dispatched + [slots.dispatch(Items().ticket("ENG-1"), routine)]

        assert asyncio.run(scenario()) == [True, False, True]

    def test_a_failed_task_is_logged_and_frees_its_slot(self, caplog: pytest.LogCaptureFixture) -> None:
        async def scenario() -> bool:
            slots = TaskSlots(asyncio.Semaphore(1), set(), set())
            slots.dispatch(Items().ticket("ENG-1"), FailingRoutine())
            await slots.drain()
            await asyncio.sleep(0)
            return slots.dispatch(Items().ticket("ENG-1"), FailingRoutine())

        assert asyncio.run(scenario()) is True
        assert "serve: task ENG-1 failed: broke on ENG-1" in caplog.text


@dataclass(frozen=True)
class FlakyJira:
    """Jira that is down for the first poll, then answers with one ticket."""

    calls: List[str] = field(default_factory=list)

    def assigned(self, project: str, limit: int) -> List[Ticket]:
        self.calls.append(project)
        if len(self.calls) == 1:
            raise SourceUnreachable("jira (acme): could not reach the source (HTTP 502)")
        return [Tickets().jira("ENG-1", "High", "2026-01-03")]


class Watching:
    """The work of a watch whose selector picks ENG-1 for `acme/app`."""

    def __init__(self, company: ServeCompany) -> None:
        self._company = company

    def work(self) -> WatchWork:
        agent = PickingAgent([{"key": "ENG-1", "repo": "acme/app", "reason": "the only ticket"}])
        return WatchWork(RecordingRoutine(self._company.state), Choice(Selector(agent, self._company), self._company))


class TestCycle:
    def test_a_failed_poll_is_logged_and_the_next_poll_works(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        """One Jira or GitHub error must not stop `serve` for every company and task."""
        jira = FlakyJira()
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources(jira_adapter=jira))
        company = Companies(tmp_path / "knowledge").company({"jira": {"ENG": {"repos": ["acme/app"]}}})
        slots = TaskSlots(asyncio.Semaphore(TWO_SLOTS), set(), set())
        watch = CompanyWatch(company, slots, Watching(company).work())

        async def scenario() -> List[int]:
            return [await watch.cycle(), await watch.cycle()]

        assert asyncio.run(scenario()) == [0, 1]
        assert "serve: acme: the poll failed, trying again next poll: jira (acme): could not reach the source (HTTP 502)" in caplog.text

    def test_a_ticket_with_a_pr_is_not_started_again_and_keeps_why_it_was_picked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jira = FakeJira({"ENG": [Tickets().jira("ENG-1", "High", "2026-01-03")]})
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources(jira_adapter=jira))
        company = Companies(tmp_path / "knowledge").company({"jira": {"ENG": {"repos": ["acme/app"]}}})
        slots = TaskSlots(asyncio.Semaphore(TWO_SLOTS), set(), set())
        watch = CompanyWatch(company, slots, Watching(company).work())

        async def scenario() -> List[int]:
            first = await watch.cycle()
            await slots.drain()
            return [first, await watch.cycle()]

        assert asyncio.run(scenario()) == [1, 0]
        expected = WorkEntry(key="ENG-1", source=WorkSource.JIRA, repo="acme/app", title="title of ENG-1", status=WorkStatus.PR_OPEN, reason="the only ticket", started=WorkDay().today())
        assert company.state.book().entries == {"ENG-1": expected}

    def test_a_ticket_briar_started_goes_on_in_its_repo_without_the_selector(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jira = FakeJira({"ENG": [Tickets().jira("ENG-1", "High", "2026-01-03")]})
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources(jira_adapter=jira))
        company = Companies(tmp_path / "knowledge").company({"jira": {"ENG": {"repos": ["acme/app", "acme/api"]}}})
        started = WorkEntry(key="ENG-1", source=WorkSource.JIRA, repo="acme/api", title="title of ENG-1", status=WorkStatus.FOUND)
        asyncio.run(company.state.record(started))
        agent = PickingAgent([])
        slots = TaskSlots(asyncio.Semaphore(TWO_SLOTS), set(), set())
        watch = CompanyWatch(company, slots, WatchWork(RecordingRoutine(company.state), Choice(Selector(agent, company), company)))

        async def scenario() -> int:
            count = await watch.cycle()
            await slots.drain()
            return count

        assert (asyncio.run(scenario()), agent.requests) == (1, [])
        entry = company.state.book().entries["ENG-1"]
        assert (entry.status, entry.repo) == (WorkStatus.PR_OPEN, "acme/api")


class TestDailyLimit:
    """Decision D3 of the autonomous serve: at most `max_new_per_day` new tickets a day per
    company (UTC days); tasks already started go on."""

    def _watch(self, company: ServeCompany, agent: PickingAgent) -> CompanyWatch:
        slots = TaskSlots(asyncio.Semaphore(TWO_SLOTS), set(), set())
        return CompanyWatch(company, slots, WatchWork(RecordingRoutine(company.state), Choice(Selector(agent, company), company)))

    def _company(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, started_on: str) -> ServeCompany:
        jira = FakeJira({"ENG": [Tickets().jira("ENG-1", "High", "2026-01-03")]})
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources(jira_adapter=jira))
        company = Companies(tmp_path / "knowledge").company({"jira": {"ENG": {"repos": ["acme/app"]}}, "max_new_per_day": 1})
        earlier = WorkEntry(key="ENG-9", source=WorkSource.JIRA, repo="acme/app", title="earlier", status=WorkStatus.DONE, started=started_on)
        asyncio.run(company.state.record(earlier))
        return company

    def test_no_new_ticket_starts_once_the_day_limit_is_reached(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="briar.serve.loop")
        company = self._company(tmp_path, monkeypatch, WorkDay().today())
        agent = PickingAgent([{"key": "ENG-1", "repo": "acme/app", "reason": "the only ticket"}])

        assert (asyncio.run(self._watch(company, agent).cycle()), agent.requests) == (0, [])
        assert "serve: acme: 1 new ticket(s) started today (max_new_per_day 1): no new ticket until tomorrow (UTC)" in caplog.text

    def test_tickets_started_on_another_day_do_not_count(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        company = self._company(tmp_path, monkeypatch, "2026-01-01")
        agent = PickingAgent([{"key": "ENG-1", "repo": "acme/app", "reason": "the only ticket"}])

        assert asyncio.run(self._watch(company, agent).cycle()) == 1
        assert company.state.book().entries["ENG-1"].started == WorkDay().today()


class TestDailyMoneyLimit:
    """Decisions B11 and B13 (PR B): with the spend booked today at `max_usd_per_day` no ticket starts and
    nothing that needs the model runs (the cap is hard; see `TestDayGate`)."""

    def _company(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spent_today: float) -> ServeCompany:
        jira = FakeJira({"ENG": [Tickets().jira("ENG-1", "High", "2026-01-03")]})
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources(jira_adapter=jira))
        company = Companies(tmp_path / "knowledge").company({"jira": {"ENG": {"repos": ["acme/app"]}}, "max_usd_per_day": DAY_CAP})
        earlier = WorkEntry(key="ENG-9", source=WorkSource.JIRA, repo="acme/app", title="earlier", status=WorkStatus.DONE, spent_by_day={WorkDay().today(): spent_today, "2026-01-01": OTHER_DAY_SPEND})
        asyncio.run(company.state.record(earlier))
        return company

    def _cycle(self, company: ServeCompany, agent: PickingAgent) -> int:
        slots = TaskSlots(asyncio.Semaphore(TWO_SLOTS), set(), set())
        watch = CompanyWatch(company, slots, WatchWork(RecordingRoutine(company.state), Choice(Selector(agent, company), company)))
        return asyncio.run(watch.cycle())

    def test_no_new_ticket_starts_once_the_day_spend_reaches_the_cap(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="briar.serve.loop")
        company = self._company(tmp_path, monkeypatch, DAY_CAP)
        agent = PickingAgent([{"key": "ENG-1", "repo": "acme/app", "reason": "the only ticket"}])

        assert (self._cycle(company, agent), agent.requests) == (0, [])
        assert f"serve: acme: {GATE_NOTE}" in caplog.text
        assert company.notes[WorkDay().today()] == [GATE_NOTE]

    def test_a_day_under_the_cap_and_spend_of_other_days_do_not_stop_a_ticket(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        company = self._company(tmp_path, monkeypatch, DAY_CAP - 1)
        agent = PickingAgent([{"key": "ENG-1", "repo": "acme/app", "reason": "the only ticket"}])

        assert self._cycle(company, agent) == 1


class KeyRoutine:
    """Records the kind and the key of every task a poll dispatches."""

    def __init__(self) -> None:
        self.dispatched: List[Tuple[WorkKind, str]] = []

    async def work_on(self, item: WorkItem) -> None:
        self.dispatched.append((item.kind, item.key))


class TestDayGate:
    """Decisions B11 and B13 (PR B): with the BOOKED spend at the cap, nothing that needs the model is dispatched, so no
    poll fetches a clone and builds a worktree for a task that would pause at its first call. A merged or closed PR
    only needs the state, so it is still recorded."""

    def watch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spent: float) -> Tuple[ServeCompany, KeyRoutine, CompanyWatch, TaskSlots]:
        today = WorkDay().today()
        review = ReviewComment(id="1", author="ann", body="rename this", kind=CommentKind.INLINE, association="MEMBER", updated_at="t1")
        github = FakeGithub(comments={("acme/app", PR): [review]}, pulls={("acme/app", MERGED_PR): PullState(False, True, False, "main")})
        jira = FakeJira({"ENG": [Tickets().jira("ENG-7", "High", "2026-01-03")]})
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources(github, jira_adapter=jira))
        company = Companies(tmp_path / "knowledge").company({"jira": {"ENG": {"repos": ["acme/app"]}}, "max_usd_per_day": DAY_CAP})
        rows = [
            ("ENG-9", WorkStatus.DONE, 0, {today: spent}),
            ("ENG-8", WorkStatus.DONE, PR, {}),
            ("ENG-7", WorkStatus.PLANNED, 0, {}),
            ("ENG-6", WorkStatus.DONE, MERGED_PR, {}),
        ]
        for key, status, pr, spent_by_day in rows:
            entry = WorkEntry(key=key, source=WorkSource.JIRA, repo="acme/app", title=key, status=status, pr=pr, spent_by_day=spent_by_day)
            asyncio.run(company.state.record(entry))
        routine, slots = KeyRoutine(), TaskSlots(asyncio.Semaphore(THREE_SLOTS), set(), set())
        return company, routine, CompanyWatch(company, slots, WatchWork(routine, Choice(Selector(PickingAgent([]), company), company))), slots

    def polls(self, watch: CompanyWatch, slots: TaskSlots, count: int) -> None:
        async def scenario() -> None:
            for _ in range(count):
                await watch.cycle()
                await slots.drain()

        asyncio.run(scenario())

    def test_a_poll_at_the_cap_dispatches_only_what_needs_no_model(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, routine, watch, slots = self.watch(tmp_path, monkeypatch, DAY_CAP)

        self.polls(watch, slots, 1)

        assert routine.dispatched == [(WorkKind.MERGED, "ENG-6")]

    def test_a_poll_under_the_cap_dispatches_the_review_the_resumed_ticket_and_the_merge(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, routine, watch, slots = self.watch(tmp_path, monkeypatch, DAY_CAP - 1)

        self.polls(watch, slots, 1)

        assert sorted(routine.dispatched, key=str) == sorted([(WorkKind.REVIEW, "ENG-8"), (WorkKind.TICKET, "ENG-7"), (WorkKind.MERGED, "ENG-6")], key=str)

    def test_many_polls_at_the_cap_leave_one_note_for_the_day(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        company, _, watch, slots = self.watch(tmp_path, monkeypatch, DAY_CAP)

        self.polls(watch, slots, THREE_POLLS)

        assert company.notes[WorkDay().today()] == [GATE_NOTE]


class TestReopenInTheCycle:
    """Decision B10: every poll brings back the tickets whose cap was raised, before it finds the work."""

    def test_a_poll_brings_back_a_ticket_that_stopped_for_money(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources())
        company = Companies(tmp_path / "knowledge").company({"repos": ["acme/app"], "max_usd": RAISED_USD})
        stopped = WorkEntry(
            key="ENG-1", source=WorkSource.JIRA, repo="acme/app", title="stopped", status=WorkStatus.BLOCKED, pr=PR,
            spent_by_day={WorkDay().today(): SPENT_AT_STOP}, money_stop=MoneyStop(cap=CAP_AT_STOP, resume_to="pr-open", drafted=True),
        )  # fmt: skip
        asyncio.run(company.state.record(stopped))
        slots = TaskSlots(asyncio.Semaphore(TWO_SLOTS), set(), set())
        watch = CompanyWatch(company, slots, WatchWork(RecordingRoutine(company.state), Choice(Selector(PickingAgent([]), company), company)))

        asyncio.run(watch.cycle())

        assert company.state.book().entry("ENG-1").status is WorkStatus.PR_OPEN


class TestState:
    def test_tasks_that_write_at_once_keep_every_entry(self, tmp_path: Path) -> None:
        company = Companies(tmp_path / "knowledge").company({"repos": ["acme/app"]})
        entries = [Items().ticket(f"ENG-{number}").entry() for number in range(FIVE_TASKS)]

        async def scenario() -> None:
            await asyncio.gather(*(company.state.record(entry) for entry in entries))

        asyncio.run(scenario())

        assert sorted(company.state.book().entries) == [f"ENG-{number}" for number in range(FIVE_TASKS)]
        assert (tmp_path / "knowledge" / "work" / "acme.md").exists()
