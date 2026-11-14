"""The loop of `briar serve` (spec 003 PR 1: S15, S16, S17): parallel tasks with a limit,
never two on the same ticket, and the state they write. No network: the finder reads
the fakes of `fakes.py`."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import pytest

from briar.auth.check import SourceUnreachable
from briar.extract._tracker import Ticket
from briar.serve.loop import CompanyWatch, TaskSlots, WatchWork
from briar.serve.select import Choice, Selector
from briar.serve.state import WorkState
from briar.serve.state import WorkEntry, WorkSource, WorkStatus
from briar.serve.work import ServeCompany, WorkDay, WorkItem, WorkKind
from tests.unit.serve.fakes import Companies, FakeJira, FakeSources, Tickets
from tests.unit.serve.test_select import PickingAgent

TWO_SLOTS = 2
# The US$ a day cap of `TestDailyMoneyLimit`.
DAY_CAP = 10.0
# A big spend on another day: it must not count against today.
OTHER_DAY_SPEND = 100.0
FIVE_TASKS = 5


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
    """Decision R9: `max_usd_per_day` stops only NEW tickets; tasks and review rounds that are
    already open go on, so nothing is left half done."""

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
        assert "serve: acme: US$ 10.00 spent today (max_usd_per_day 10.0): no new ticket until tomorrow (UTC)" in caplog.text
        assert company.notes[WorkDay().today()] == ["US$ 10.00 spent today (max_usd_per_day 10.0): no new ticket until tomorrow (UTC)"]

    def test_a_day_under_the_cap_and_spend_of_other_days_do_not_stop_a_ticket(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        company = self._company(tmp_path, monkeypatch, DAY_CAP - 1)
        agent = PickingAgent([{"key": "ENG-1", "repo": "acme/app", "reason": "the only ticket"}])

        assert self._cycle(company, agent) == 1


class TestState:
    def test_tasks_that_write_at_once_keep_every_entry(self, tmp_path: Path) -> None:
        company = Companies(tmp_path / "knowledge").company({"repos": ["acme/app"]})
        entries = [Items().ticket(f"ENG-{number}").entry() for number in range(FIVE_TASKS)]

        async def scenario() -> None:
            await asyncio.gather(*(company.state.record(entry) for entry in entries))

        asyncio.run(scenario())

        assert sorted(company.state.book().entries) == [f"ENG-{number}" for number in range(FIVE_TASKS)]
        assert (tmp_path / "knowledge" / "work" / "acme.md").exists()
