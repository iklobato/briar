"""The hard daily cap of `briar serve` (decisions B11 to B14 and B18 of
docs/decisions/2026-11-18-serve-spend-caps.md, PR B): each call gets `min(ticket left, day left
minus what other calls in flight hold)`; a call with no room waits while the BOOKED spend is under
the cap and pauses only when it reaches the cap; a paused task writes nothing on the ticket."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import replace
from pathlib import Path
from typing import List, Optional, Tuple

import pytest
import time_machine

from briar.errors import CliError
from briar.extract._provider import CiState
from briar.serve.day import DayCap, DayPause, Grant
from briar.serve.git import Shell
from briar.serve.routine import ServeRoutine
from briar.serve.state import WorkBook, WorkEntry, WorkSource, WorkStatus
from briar.serve.task import TaskContext
from briar.serve.work import ServeCompanies, ServeCompany, WorkDay, WorkItem
from tests.unit.serve.fakes import Companies
from tests.unit.serve.ticket_fakes import BrokenAgent, FakeGithubSide, Files, Items, Plans, ScriptedAgent, Settings, Setup, Setups, Step

CAP = 10.0
TICKET = 5.0
# More than any cap of these tests, and what is left of the cap after most of it was booked.
WAY_OVER = 999.0
PLENTY = 100.0
DAY_LEFT = 2.0
# What one call really costs, below its grant.
REAL_COST = 0.3
# The `max_usd_per_day` of the runbook of `TestRealRunbook`.
RUNBOOK_CAP = 3.0
# The tickets left of two calls in flight that leave 0.3 of a cap of 10, and a ticket of 3.3 (three of them hold 9.9).
ALMOST_FIVE = 4.9
JUST_UNDER = 4.8
THIRD = 3.3
# Loop turns that let a waiting task wake and wait again.
SETTLE = 3
# A call the SDK stops a hair under its grant, and a cap that fits the plan call and leaves 0.05.
HAIR = 0.01
AFTER_PLAN = 0.15
# What the selector call cost, and the default cost of one scripted step.
SELECTION = 0.04
STEP = 0.1
TASKS = 3
WAIT_SECONDS = 2
DAY_ONE = "2030-01-01"
DAY_ONE_NOON = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
DAY_TWO_START = dt.datetime(2030, 1, 2, 0, 0, 5, tzinfo=dt.timezone.utc)
# What the first three steps of a ticket book, with the default cost of a step.
THREE_STEPS = 0.3
# A day cap that leaves 0.05 after those three steps, and what the fix call spends before the SDK stops it.
LEAVES_LITTLE = 0.35
LITTLE = 0.05
# A day cap so small that the first call is stopped by it.
TINY = 0.3
PR = FakeGithubSide.NUMBER
STOP = "error_max_budget_usd"
MESSAGE = "the daily cap max_usd_per_day of US$ 10 is reached"
PAUSED = f"ENG-1 paused: {MESSAGE}; it goes on tomorrow (UTC) or when the cap is raised"


class Settled:
    """Lets the other tasks of the loop run until they wait again."""

    async def loop(self) -> None:
        for _ in range(SETTLE):
            await asyncio.sleep(0)


class Booked:
    """What the state says was booked today. A test moves it."""

    def __init__(self, amount: float) -> None:
        self.amount = amount

    def __call__(self) -> float:
        return self.amount


class Elsewhere:
    """Another ticket of the company, that spent money today."""

    def entry(self) -> WorkEntry:
        return WorkEntry(key="ENG-9", source=WorkSource.JIRA, repo="acme/app", title="earlier", status=WorkStatus.DONE)

    def spend(self, company: ServeCompany, amount: float, day: str) -> None:
        asyncio.run(company.state.add_spend(self.entry(), day, amount))


class Shape:
    """What a grant of a company holds, without the day it was granted on: `TestGrantDay` pins that."""

    def of(self, grant: Optional[Grant]) -> Optional[Tuple[float, float]]:
        return None if grant is None else (grant.amount, grant.held)


class TestGrantDay:
    """Audit 6: a grant carries the UTC day it was made on, also when the call returns after midnight."""

    def test_a_grant_and_an_offer_carry_the_day_they_were_made_on(self, tmp_path: Path) -> None:
        company = Companies(tmp_path / "knowledge").company({**Settings().block([]), "max_usd_per_day": CAP})
        with time_machine.travel(DAY_ONE_NOON, tick=False):
            granted, offered = asyncio.run(company.grant(THIRD)), company.offer(THIRD)

        assert (granted.day, offered is not None and offered.day) == (DAY_ONE, DAY_ONE)


class TestDayCap:
    def test_with_the_cap_off_the_grant_is_what_the_ticket_has_left(self) -> None:
        grant = asyncio.run(DayCap().grant(0.0, Booked(WAY_OVER), TICKET))

        assert grant == Grant(TICKET, 0.0)

    def test_a_grant_is_the_smaller_of_what_the_ticket_and_the_day_have_left(self) -> None:
        grant = asyncio.run(DayCap().grant(CAP, Booked(CAP - 1.0), TICKET))

        assert grant == Grant(1.0, 1.0)

    def test_calls_in_flight_hold_their_share_of_the_day(self) -> None:
        async def scenario() -> Tuple[Grant, Grant]:
            cap, booked = DayCap(), Booked(0.0)
            return await cap.grant(CAP, booked, TICKET), await cap.grant(CAP, booked, TICKET)

        assert asyncio.run(scenario()) == (Grant(TICKET, TICKET), Grant(TICKET, TICKET))

    def test_a_third_call_waits_for_a_release_and_does_not_pause(self) -> None:
        """Cap 10, ticket 5, three tasks: the third finds all the day reserved while nothing is booked."""

        async def scenario() -> Tuple[bool, Grant]:
            cap, booked = DayCap(), Booked(0.0)
            first = await cap.grant(CAP, booked, TICKET)
            await cap.grant(CAP, booked, TICKET)
            third = asyncio.ensure_future(cap.grant(CAP, booked, TICKET))
            await asyncio.sleep(0)
            waiting = not third.done()
            cap.release(first)
            return waiting, await asyncio.wait_for(third, timeout=1)

        assert asyncio.run(scenario()) == (True, Grant(TICKET, TICKET))

    def test_a_waiting_call_pauses_when_the_booked_spend_reaches_the_cap(self) -> None:
        async def scenario() -> str:
            cap, booked = DayCap(), Booked(0.0)
            first = await cap.grant(CAP, booked, TICKET)
            await cap.grant(CAP, booked, TICKET)
            third = asyncio.ensure_future(cap.grant(CAP, booked, TICKET))
            await asyncio.sleep(0)
            booked.amount = CAP
            cap.release(first)
            with pytest.raises(DayPause) as stop:
                await asyncio.wait_for(third, timeout=1)
            return str(stop.value)

        assert asyncio.run(scenario()) == MESSAGE

    def test_nothing_is_granted_once_the_booked_spend_is_at_the_cap(self) -> None:
        with pytest.raises(DayPause) as stop:
            asyncio.run(DayCap().grant(CAP, Booked(CAP), TICKET))

        assert str(stop.value) == MESSAGE

    def test_a_cancelled_waiting_call_holds_nothing(self) -> None:
        async def scenario() -> Grant:
            cap, booked = DayCap(), Booked(0.0)
            first = await cap.grant(CAP, booked, TICKET)
            second = await cap.grant(CAP, booked, TICKET)
            third = asyncio.ensure_future(cap.grant(CAP, booked, TICKET))
            await asyncio.sleep(0)
            third.cancel()
            cap.release(first)
            cap.release(second)
            return await cap.grant(CAP, booked, CAP)

        assert asyncio.run(scenario()) == Grant(CAP, CAP)

    def test_a_release_gives_back_what_the_grant_held(self) -> None:
        async def scenario() -> Grant:
            cap, booked = DayCap(), Booked(0.0)
            cap.release(await cap.grant(CAP, booked, TICKET))
            return await cap.grant(CAP, booked, CAP)

        assert asyncio.run(scenario()) == Grant(CAP, CAP)

    def test_a_call_does_not_take_the_scraps_that_calls_in_flight_leave(self) -> None:
        """Cap 10 with 4.9 and 4.8 in flight: a third call would get 0.3, which the SDK stops early while the booked spend is
        far under the cap, so the task would pause and say the day cap was hit. It waits for a release instead."""

        async def scenario() -> Tuple[bool, Grant]:
            cap, booked = DayCap(), Booked(0.0)
            first = await cap.grant(CAP, booked, ALMOST_FIVE)
            second = await cap.grant(CAP, booked, JUST_UNDER)
            third = asyncio.ensure_future(cap.grant(CAP, booked, TICKET))
            await Settled().loop()
            waiting = not third.done()
            cap.release(first)
            cap.release(second)
            return waiting, await asyncio.wait_for(third, timeout=1)

        assert asyncio.run(scenario()) == (True, Grant(TICKET, TICKET))

    def test_an_offer_is_none_while_calls_in_flight_hold_the_room_and_never_waits(self) -> None:
        async def scenario() -> Optional[Grant]:
            cap, booked = DayCap(), Booked(0.0)
            await cap.grant(CAP, booked, TICKET)
            await cap.grant(CAP, booked, TICKET)
            return cap.offer(CAP, booked, TICKET)

        assert asyncio.run(scenario()) is None

    def test_an_offer_raises_when_the_booked_spend_is_at_the_cap(self) -> None:
        with pytest.raises(DayPause) as stop:
            DayCap().offer(CAP, Booked(CAP), TICKET)

        assert str(stop.value) == MESSAGE

    def test_float_residue_never_leaves_a_waiting_call_with_nobody_to_wake_it(self) -> None:
        """Three calls of 3.3 hold 9.9 of 10, the fourth waits; releasing all three must wake it with a whole grant."""

        async def scenario() -> Grant:
            cap, booked = DayCap(), Booked(0.0)
            held = [await cap.grant(CAP, booked, THIRD) for _ in range(TASKS)]
            fourth = asyncio.ensure_future(cap.grant(CAP, booked, THIRD))
            await Settled().loop()
            for grant in held:
                cap.release(grant)
            return await asyncio.wait_for(fourth, timeout=1)

        assert asyncio.run(scenario()) == Grant(THIRD, THIRD)


class TestOnACompany:
    """The grant of a company reads the spend booked today in its state."""

    def company(self, tmp_path: Path) -> ServeCompany:
        return Companies(tmp_path / "knowledge").company({**Settings().block([]), "max_usd_per_day": CAP})

    def test_the_day_left_comes_from_the_spend_booked_today(self, tmp_path: Path) -> None:
        company = self.company(tmp_path)
        Elsewhere().spend(company, CAP - DAY_LEFT, WorkDay().today())

        assert Shape().of(asyncio.run(company.grant(TICKET))) == (DAY_LEFT, DAY_LEFT)

    def test_spend_of_another_day_does_not_count(self, tmp_path: Path) -> None:
        company = self.company(tmp_path)
        Elsewhere().spend(company, PLENTY, "2026-01-01")

        assert Shape().of(asyncio.run(company.grant(TICKET))) == (TICKET, TICKET)

    def test_the_third_task_waits_for_both_and_then_gets_its_whole_ticket(self, tmp_path: Path) -> None:
        """What each call really spent is booked before its reservation is released. While the second still holds its
        share the third does not take what is left (4.7): it waits, and it never pauses with the booked spend this low."""
        company = self.company(tmp_path)

        async def scenario() -> Tuple[bool, Optional[Tuple[float, float]]]:
            first = await company.grant(TICKET)
            second = await company.grant(TICKET)
            third = asyncio.ensure_future(company.grant(TICKET))
            await company.state.add_spend(Elsewhere().entry(), WorkDay().today(), REAL_COST)
            company.day.release(first)
            await Settled().loop()
            waiting = not third.done()
            await company.state.add_spend(Elsewhere().entry(), WorkDay().today(), REAL_COST)
            company.day.release(second)
            return waiting, Shape().of(await asyncio.wait_for(third, timeout=1))

        assert asyncio.run(scenario()) == (True, (TICKET, TICKET))

    def test_the_company_is_capped_once_the_booked_spend_reaches_the_cap(self, tmp_path: Path) -> None:
        company = self.company(tmp_path)
        before = company.capped()
        Elsewhere().spend(company, CAP, WorkDay().today())

        assert (before, company.capped()) == (False, True)

    def test_a_company_with_the_cap_off_is_never_capped(self, tmp_path: Path) -> None:
        company = Companies(tmp_path / "knowledge").company(Settings().block([]))
        Elsewhere().spend(company, WAY_OVER, WorkDay().today())

        assert company.capped() is False


@pytest.fixture
def setup(setups: Setups) -> Setup:
    return setups.make({"max_usd_per_day": CAP})


class TestTicketPause:
    """B13: the day cap pauses a task, it does not block it; nothing is written on the ticket or the PR."""

    def spent(self, setup: Setup) -> None:
        Elsewhere().spend(setup.company, CAP, WorkDay().today())

    def silent(self, setup: Setup) -> Tuple[object, ...]:
        github, tracker = setup.sources.github_side, setup.sources.tracker_side
        return github.pulls, github.comments, github.drafts, github.readies, tracker.comments, tracker.moves

    def test_no_call_is_made_and_nothing_is_written_once_the_day_is_spent(self, setup: Setup) -> None:
        self.spent(setup)
        agent = ScriptedAgent([Plans().plan()])

        setup.work_on(agent, Items().jira())

        assert agent.requests == []
        assert self.silent(setup) == ([], [], [], [], [], [])
        assert setup.entry("ENG-1") is None

    def test_the_pause_leaves_one_note_for_the_daily_report(self, setup: Setup) -> None:
        self.spent(setup)

        setup.work_on(ScriptedAgent([]), Items().jira())

        assert setup.company.notes[WorkDay().today()] == [PAUSED]

    def test_a_call_the_sdk_stops_at_the_day_grant_pauses_the_ticket_and_does_not_block_it(self, setups: Setups) -> None:
        setup = setups.make({"max_usd_per_day": TINY})
        agent = ScriptedAgent([Step(cost=TINY, failed=True, reason=STOP)])

        setup.work_on(agent, Items().jira())

        entry = setup.entry("ENG-1")
        assert agent.requests[0].max_usd == TINY
        assert (entry.status, entry.report, entry.money_stop, entry.spent_total()) == (WorkStatus.FOUND, "", None, TINY)
        assert self.silent(setup) == ([], [], [], [], [], [])

    def test_the_ticket_cap_still_blocks_when_it_is_the_smaller_limit(self, setups: Setups) -> None:
        """The day has room (cap 100): the SDK stop is the ticket cap, which blocks as in PR A."""
        setup = setups.make({"max_usd_per_day": PLENTY})

        setup.work_on(ScriptedAgent([Step(cost=TICKET, failed=True, reason=STOP)]), Items().jira())

        assert setup.entry("ENG-1").status is WorkStatus.BLOCKED

    def test_a_paused_ticket_goes_on_when_the_cap_is_raised(self, setups: Setups) -> None:
        setup = setups.make({"max_usd_per_day": TINY})
        setup.work_on(ScriptedAgent([Step(cost=TINY, failed=True, reason=STOP)]), Items().jira())
        raised = setup.day_capped(CAP)

        raised.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        assert raised.entry("ENG-1").status is WorkStatus.DONE

    def test_a_paused_ticket_goes_on_the_next_utc_day(self, setup: Setup) -> None:
        with time_machine.travel(DAY_ONE_NOON, tick=True):
            Elsewhere().spend(setup.company, CAP, WorkDay().today())
            setup.work_on(ScriptedAgent([]), Items().jira())
        steps = [Plans().plan(), Step(writes=Files().feature()), Plans().approved()]

        with time_machine.travel(DAY_TWO_START, tick=True):
            setup.work_on(ScriptedAgent(steps), Items().jira())

        assert setup.entry("ENG-1").status is WorkStatus.DONE


class TestTheDayIsSpent:
    """The SDK may stop a call a hair under its grant: the booked spend then stays under the cap, and the gate must still close."""

    def test_a_call_stopped_a_hair_under_the_day_grant_still_closes_the_gate(self, setups: Setups) -> None:
        setup = setups.make({"max_usd_per_day": TINY})

        setup.work_on(ScriptedAgent([Step(cost=TINY - HAIR, failed=True, reason=STOP)]), Items().jira())

        assert (setup.company.capped(), setup.company.spent_today()) == (True, TINY)

    def test_a_new_day_opens_the_gate_again(self, setups: Setups) -> None:
        setup = setups.make({"max_usd_per_day": TINY})
        with time_machine.travel(DAY_ONE_NOON, tick=True):
            setup.work_on(ScriptedAgent([Step(cost=TINY - HAIR, failed=True, reason=STOP)]), Items().jira())

        with time_machine.travel(DAY_TWO_START, tick=True):
            assert setup.company.capped() is False


class TestPauseWithNoPr:
    def test_the_half_built_edits_stay_for_the_next_run(self, setups: Setups) -> None:
        """A ticket with no PR yet keeps what the build wrote: the plan call booked 0.1 and the cap leaves 0.05 for the build."""
        setup = setups.make({}).day_capped(AFTER_PLAN)
        build = Step(writes={"wip.txt": "half\n"}, cost=LITTLE, failed=True, reason=STOP)

        setup.work_on(ScriptedAgent([Plans().plan(), build]), Items().jira())

        worktree = setup.cache / "worktrees" / "acme" / "briar__ENG-1"
        assert ((worktree / "wip.txt").read_text(), setup.entry("ENG-1").status) == ("half\n", WorkStatus.PLANNED)


class TestReservationIsReleased:
    """A reservation outlives nothing: with a cap of one ticket a leak would leave no room for the next call."""

    def room_left(self, setup: Setup) -> object:
        return setup.company.offer(TICKET)

    def test_when_the_agent_raises(self, setups: Setups) -> None:
        setup = setups.make({"max_usd_per_day": TICKET})

        setup.work_on(BrokenAgent([]), Items().jira())

        assert Shape().of(self.room_left(setup)) == (TICKET, TICKET)

    def test_when_the_booking_fails(self, setups: Setups, monkeypatch: pytest.MonkeyPatch) -> None:
        setup = setups.make({"max_usd_per_day": TICKET})

        async def disk_full(fresh: WorkEntry, day: str, cost: float) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(setup.company.state, "add_spend", disk_full)

        setup.work_on(ScriptedAgent([Plans().plan()]), Items().jira())

        assert Shape().of(self.room_left(setup)) == (TICKET, TICKET)


class TestRealRunbook:
    """The `max_usd_per_day` of a runbook reaches the grant through the real loader."""

    RUNBOOK = (
        "version: 1\ncompanies:\n  acme:\n    knowledge:\n      store: file\n      name: knowledge:acme\n"
        "    serve:\n      repos: [acme/app]\n      model: claude-test\n      max_usd: 5\n      max_loops: 1\n"
        "      ci_wait_minutes: 1\n      max_review_rounds: 1\n      max_usd_per_day: 3\n"
    )

    def test_the_cap_of_the_runbook_cuts_the_grant_of_a_lone_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "runbooks").mkdir()
        (tmp_path / "runbooks" / "acme.yaml").write_text(self.RUNBOOK)
        monkeypatch.chdir(tmp_path)

        (company,) = ServeCompanies(Path("runbooks"), {}).load()

        assert (company.settings.max_usd_per_day, Shape().of(asyncio.run(company.grant(TICKET)))) == (RUNBOOK_CAP, (RUNBOOK_CAP, RUNBOOK_CAP))


class TestPauseWithAPr:
    """A pause with a PR drops what was never verified (the next sync is a fast forward only) and keeps the draft."""

    def worktree(self, setup: Setup) -> Path:
        return setup.cache / "worktrees" / "acme" / "briar__ENG-1"

    def paused(self, setups: Setups) -> Setup:
        """A ticket whose CI is red, and whose CI fix is stopped by the day grant."""
        setup = setups.make({}).day_capped(LEAVES_LITTLE)
        setup.sources.github_side.ci[:] = [CiState.FAILED]
        fix = Step(writes={"other.txt": "x\n"}, cost=LITTLE, failed=True, reason=STOP)
        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved(), fix]), Items().jira())
        return setup

    def test_the_unverified_fix_is_dropped_and_the_ticket_is_not_blocked(self, setups: Setups) -> None:
        setup = self.paused(setups)

        entry, worktree = setup.entry("ENG-1"), self.worktree(setup)

        assert (entry.status, entry.pr, entry.report) == (WorkStatus.PR_OPEN, PR, "")
        assert not (worktree / "other.txt").exists()
        assert Shell().line("git status --porcelain", worktree).output == ""

    def test_the_pr_stays_a_draft_and_nobody_is_asked_to_review(self, setups: Setups) -> None:
        github = self.paused(setups).sources.github_side

        assert (github.readies, github.drafts, github.asked) == ([], [], [])

    def test_the_draft_is_finished_after_the_cap_is_raised(self, setups: Setups) -> None:
        setup = self.paused(setups)
        raised = setup.day_capped(CAP)
        raised.sources.github_side.ci[:] = [CiState.PASSED]

        raised.work_on(ScriptedAgent([]), Items().ready(PR))

        assert (raised.entry("ENG-1").status, raised.sources.github_side.readies) == (WorkStatus.DONE, [PR])


class BrokenPrep:
    """A `TaskPrep` whose git fetch fails: the task never starts."""

    def context(self, company: ServeCompany, item: WorkItem) -> TaskContext:
        raise CliError("git fetch failed")

    def review_context(self, company: ServeCompany, item: WorkItem) -> TaskContext:
        raise CliError("git fetch failed")


class TestSelectionClaim:
    """The selector cost is booked to the day when its call returns; a pick that becomes a task MOVES it to the entry."""

    def test_a_pick_moves_its_cost_to_the_entry_and_the_day_total_stays_the_same(self, setups: Setups) -> None:
        setup, day = setups.make({}), WorkDay().today()
        asyncio.run(setup.company.state.add_loose(day, SELECTION))
        item = replace(Items().jira(), selection_cost=SELECTION, selection_day=day)

        setup.work_on(ScriptedAgent([Plans().no_code()]), item)

        book = setup.company.state.book()
        assert (book.loose_by_day[day], book.entry("ENG-1").spent_by_day[day]) == (0.0, pytest.approx(SELECTION + STEP))
        assert book.spent_on(day) == pytest.approx(SELECTION + STEP)

    def test_a_failed_prep_after_the_pick_leaves_the_cost_counted_in_the_day(self, setup: Setup) -> None:
        day = WorkDay().today()
        asyncio.run(setup.company.state.add_loose(day, SELECTION))
        item = replace(Items().jira(), selection_cost=SELECTION, selection_day=day)

        with pytest.raises(CliError) as failed:
            asyncio.run(ServeRoutine(setup.company, lambda context: ScriptedAgent([]), BrokenPrep()).work_on(item))  # type: ignore[arg-type]

        assert str(failed.value) == "git fetch failed"
        assert setup.company.state.book().spent_on(day) == SELECTION


class TestLedger:
    """The spend of the selector with no ticket, and the new marker, in the state of the company."""

    def company(self, tmp_path: Path) -> ServeCompany:
        return Companies(tmp_path / "knowledge").company(Settings().block([]))

    def test_the_loose_spend_adds_up_and_counts_in_the_day_total(self, tmp_path: Path) -> None:
        company, day = self.company(tmp_path), WorkDay().today()
        asyncio.run(company.state.add_loose(day, SELECTION))
        asyncio.run(company.state.add_loose(day, SELECTION))
        Elsewhere().spend(company, REAL_COST, day)

        assert company.state.book().spent_on(day) == pytest.approx(SELECTION + SELECTION + REAL_COST)

    def test_recording_an_entry_keeps_the_loose_spend(self, tmp_path: Path) -> None:
        company, day = self.company(tmp_path), WorkDay().today()
        asyncio.run(company.state.add_loose(day, SELECTION))

        asyncio.run(company.state.record(Elsewhere().entry()))

        assert company.state.book().loose_by_day == {day: SELECTION}

    def test_an_old_state_with_none_of_the_new_fields_loads(self) -> None:
        old = '{"entries": {"ENG-1": {"key": "ENG-1", "source": "jira", "repo": "acme/app", "title": "t", "status": "pr-open"}}}'

        book = WorkBook.model_validate_json(old)

        assert (book.loose_by_day, book.entry("ENG-1").ci_paused) == ({}, False)


class ThreeTasks:
    """Three tasks of one company whose calls each ask for the whole ticket, and each spends all of it."""

    def __init__(self, company: ServeCompany) -> None:
        self._company = company
        self.granted: List[float] = []

    async def task(self, number: int, release: asyncio.Event) -> None:
        grant = await self._company.grant(TICKET)
        self.granted.append(grant.amount)
        await release.wait()
        entry = Elsewhere().entry().model_copy(update={"key": f"ENG-{number}"})
        await self._company.state.add_spend(entry, WorkDay().today(), grant.amount)
        self._company.day.release(grant)

    async def spend_the_whole_ticket(self) -> List[object]:
        release = asyncio.Event()
        running = [asyncio.ensure_future(self.task(number, release)) for number in range(TASKS)]
        await asyncio.sleep(0)
        release.set()
        return list(await asyncio.wait_for(asyncio.gather(*running, return_exceptions=True), timeout=WAIT_SECONDS))


class TestParallelTasks:
    """Cap 10 and ticket 5: two tasks fit at once and spend the whole day; the third must not get a call."""

    def three_tasks_run(self, tmp_path: Path) -> Tuple[ThreeTasks, List[object], ServeCompany]:
        company = Companies(tmp_path / "knowledge").company({**Settings().block([]), "max_usd_per_day": CAP})
        tasks = ThreeTasks(company)
        return tasks, asyncio.run(tasks.spend_the_whole_ticket()), company

    def test_the_first_two_get_the_whole_ticket_and_the_day_ends_at_the_cap(self, tmp_path: Path) -> None:
        tasks, _, company = self.three_tasks_run(tmp_path)

        assert (tasks.granted, company.state.book().spent_on(WorkDay().today())) == ([TICKET, TICKET], CAP)

    def test_the_third_pauses_when_the_booked_spend_reaches_the_cap(self, tmp_path: Path) -> None:
        outcomes = self.three_tasks_run(tmp_path)[1]

        assert [str(outcome) if isinstance(outcome, DayPause) else outcome for outcome in outcomes] == [None, None, MESSAGE]
