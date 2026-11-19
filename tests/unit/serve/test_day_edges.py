"""Edges of the day cap and of the money ledger that the audit of #133, #134 and #135 found (hardening PR): a selector that
did not run is not remembered as one that ran, the day a call is granted on is the day it closes, what a claim moves,
the order of booking and release, and what the selector leaves behind. Each test names the audit finding or the mutant."""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import time_machine

from briar.serve.agent import AgentAnswer, AgentRequest
from briar.serve.day import DayCap, Grant
from briar.serve.select import Choice, Selector
from briar.serve.state import WorkEntry, WorkSource, WorkStatus
from briar.serve.work import WorkDay, WorkItem
from tests.unit.serve.fakes import Companies
from tests.unit.serve.test_day_cap import Booked, Elsewhere
from tests.unit.serve.test_select import PickingAgent, Tickets
from tests.unit.serve.ticket_fakes import Entries, Files, Items, Plans, ScriptedAgent, Setups, Step

STOP = "error_max_budget_usd"
# A day cap with room for two calls of `max_usd` 5 and two dollars more, and the two calls that hold it.
ROOM_FOR_TWO = 12.0
CALLS_IN_FLIGHT = 2
# A day cap that is spent by one booking, and one that leaves 0.3 for the call that crosses midnight.
SPENT_DAY_CAP = 2.0
MIDNIGHT_CAP = 10.0
STEP = 0.1
CALL_COST = 0.3
# The day cap of the selector that the SDK stops a hair under its grant.
SELECTOR_CAP = 0.3
HAIR = 0.01
# What the selector call costs, and what a loose entry holds.
LOOSE = 0.04
CLAIMED = 0.02
OLD_DAY = "2026-01-01"
DAY_ONE = "2030-01-01"
DAY_ONE_NOON = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
DAY_ONE_LATE = dt.datetime(2030, 1, 1, 23, 59, 55, tzinfo=dt.timezone.utc)
DAY_TWO_EARLY = dt.datetime(2030, 1, 2, 0, 0, 10, tzinfo=dt.timezone.utc)
# The ticket of `TestTotalOverDays`: what an earlier day spent, and the two steps of today that pass `max_usd` together.
EARLIER_DAYS = 4.6
# The calls of the residue test: two that end and leave 5.5e-17 behind, and two that fit the cap exactly.
RESIDUE_CAP = 0.5
SMALL_CALL = 0.1
LARGE_CALL = 0.3
EXACT_FIT = 0.25
EXACT_FITS = 2
# A call that holds 0.9999999999999999 of a cap of 1.0, one whose 1e-17 the float sum loses, and a ticket above the cap.
WHOLE_CAP = 1.0
ALMOST_WHOLE = 0.9999999999999999
VANISHING = 1e-17
TICKET_OVER_THE_CAP = 5.0
# What a pick of `max_usd` 5 needs, and a day cap that fits it with room to spare.
ROOM_FOR_ONE = 6.0
MARKS = 3
NOTHING = 0.0


class Runbook:
    """The company of the selector tests: Jira project ENG with two repos, under a day cap."""

    def two_repos(self, day_cap: float) -> Dict[str, Any]:
        return {"jira": {"ENG": {"repos": ["acme/app", "acme/api"]}}, "max_usd_per_day": day_cap}

    def only_pick(self) -> List[Dict[str, str]]:
        return [{"key": "ENG-1", "repo": "acme/app", "reason": "the only ticket"}]


class AgentCrashed(Exception):
    """What a model call that dies raises."""


class TestChoiceMemoAfterSkip:
    """Audit 5: `Choice` stored the fingerprint before the selector ran, so a selector that skipped for lack of room (or for a
    spent day) was never asked again with the same shortlist, not even the next day."""

    def test_a_selector_that_skipped_for_lack_of_room_is_asked_again_once_the_room_is_free(self, tmp_path: Path) -> None:
        company = Companies(tmp_path).company(Runbook().two_repos(ROOM_FOR_TWO))
        agent = PickingAgent(Runbook().only_pick())
        choice, found = Choice(Selector(agent, company), company), [Tickets().plain("ENG-1")]

        async def scenario() -> Tuple[List[WorkItem], List[WorkItem]]:
            holds = [await company.grant(company.settings.max_usd) for _ in range(CALLS_IN_FLIGHT)]
            skipped = await choice.choose(found, 1)
            for hold in holds:
                company.day.release(hold)
            return skipped, await choice.choose(found, 1)

        skipped, again = asyncio.run(scenario())

        assert (skipped, [item.key for item in again], len(agent.requests)) == ([], ["ENG-1"], 1)

    def test_a_selector_that_skipped_for_a_spent_day_is_asked_again_the_next_day(self, tmp_path: Path) -> None:
        company = Companies(tmp_path).company(Runbook().two_repos(SPENT_DAY_CAP))
        agent = PickingAgent(Runbook().only_pick())
        choice, found = Choice(Selector(agent, company), company), [Tickets().plain("ENG-1")]
        with time_machine.travel(DAY_ONE_NOON, tick=False):
            Elsewhere().spend(company, SPENT_DAY_CAP, WorkDay().today())
            skipped = asyncio.run(choice.choose(found, 1))
        with time_machine.travel(DAY_TWO_EARLY, tick=False):
            again = asyncio.run(choice.choose(found, 1))

        assert (skipped, [item.key for item in again], len(agent.requests)) == ([], ["ENG-1"], 1)

    def test_a_selector_that_ran_and_picked_nothing_is_still_asked_once_for_the_same_shortlist(self, tmp_path: Path) -> None:
        """The memo stays for a call that ran: a poll with nothing new costs nothing."""
        company = Companies(tmp_path).company(Runbook().two_repos(ROOM_FOR_TWO))
        agent = PickingAgent([])
        choice, found = Choice(Selector(agent, company), company), [Tickets().plain("ENG-1")]

        async def scenario() -> None:
            await choice.choose(found, 1)
            await choice.choose(found, 1)

        asyncio.run(scenario())

        assert len(agent.requests) == 1


class StoppedAtGrant:
    """The selector model that the SDK stops a hair under its grant."""

    async def ask(self, request: AgentRequest) -> AgentAnswer:
        return AgentAnswer("", {}, request.max_usd - HAIR, failed=True, reason=STOP)


class TestSelectorStoppedAtTheDayGrant:
    """Audit 11: the selector stopped by the SDK at its day grant did not mark the day as spent, so a stop a hair under the cap
    left the gate open and the next poll paid for another call."""

    def test_the_day_is_marked_spent_so_the_next_poll_does_not_pay_another_call(self, tmp_path: Path) -> None:
        company = Companies(tmp_path).company(Runbook().two_repos(SELECTOR_CAP))

        asyncio.run(Selector(StoppedAtGrant(), company).pick([Tickets().plain("ENG-1")], 1, {}))

        assert company.capped() is True

    def test_a_selector_stopped_at_its_own_max_usd_leaves_the_day_open(self, tmp_path: Path) -> None:
        """Mutant n15: the day closes only when the DAY cut the grant below `max_usd`. A stop at the grant of `max_usd` itself is the
        selector limit, with most of the day left."""
        company = Companies(tmp_path).company(Runbook().two_repos(ROOM_FOR_TWO))

        asyncio.run(Selector(StoppedAtGrant(), company).pick([Tickets().plain("ENG-1")], 1, {}))

        assert company.capped() is False


class CrossesMidnight(ScriptedAgent):
    """Moves the clock past 00:00 UTC while its call runs."""

    traveller: Any = None

    async def ask(self, request: AgentRequest) -> AgentAnswer:
        answer = await super().ask(request)
        self.traveller.move_to(DAY_TWO_EARLY)
        return answer


class TestACallThatCrossesMidnight:
    """Audit 6: a call granted on day D and cut by its day grant after 00:00 UTC marked D+1 as spent, and closed the whole next
    day with a few cents of the cap booked, until a restart."""

    def test_the_day_it_was_granted_on_closes_and_the_next_day_stays_open(self, setups: Setups) -> None:
        setup = setups.make({"max_usd_per_day": MIDNIGHT_CAP})
        with time_machine.travel(DAY_ONE_LATE, tick=False) as clock:
            Elsewhere().spend(setup.company, MIDNIGHT_CAP - CALL_COST, WorkDay().today())
            agent = CrossesMidnight([Step(cost=CALL_COST, failed=True, reason=STOP)])
            agent.traveller = clock
            setup.work_on(agent, Items().jira())
        with time_machine.travel(DAY_TWO_EARLY, tick=False):
            assert (setup.company.day.exhausted(DAY_ONE), setup.company.capped()) == (True, False)


class TestClaim:
    """Mutants m01 to m03: a pick that becomes a task moves its cost from the loose ledger to the entry, on the day of the call."""

    def entry(self, key: str = "ENG-1") -> WorkEntry:
        return WorkEntry(key=key, source=WorkSource.JIRA, repo="acme/app", title="t", status=WorkStatus.FOUND)

    def test_two_picks_of_one_call_keep_the_day_total(self, tmp_path: Path) -> None:
        company, day = Companies(tmp_path).company({"repos": ["acme/app"]}), WorkDay().today()
        asyncio.run(company.state.add_loose(day, LOOSE))
        asyncio.run(company.state.claim_selection(self.entry("ENG-1"), day, CLAIMED))

        assert company.state.book().spent_on(day) == pytest.approx(LOOSE)

    def test_a_claim_with_no_loose_entry_does_not_make_the_loose_spend_negative(self, tmp_path: Path) -> None:
        company, day = Companies(tmp_path).company({"repos": ["acme/app"]}), WorkDay().today()

        asyncio.run(company.state.claim_selection(self.entry(), day, CLAIMED))

        assert company.state.book().loose_by_day.get(day, NOTHING) >= NOTHING

    def test_a_pick_claimed_on_another_day_moves_its_cost_on_the_day_of_its_call(self, setups: Setups) -> None:
        setup = setups.make({})
        asyncio.run(setup.company.state.add_loose(OLD_DAY, LOOSE))
        item = replace(Items().jira(), selection_cost=LOOSE, selection_day=OLD_DAY)

        setup.work_on(ScriptedAgent([Plans().no_code()]), item)

        book = setup.company.state.book()
        assert (book.loose_by_day[OLD_DAY], book.spent_on(OLD_DAY), book.spent_on(WorkDay().today())) == (NOTHING, pytest.approx(LOOSE), pytest.approx(STEP))


class TestTotalOverDays:
    """Mutant m10: what the ticket spent on earlier days counts against `max_usd`."""

    def test_what_the_ticket_spent_on_earlier_days_counts_against_max_usd(self, setups: Setups) -> None:
        setup = setups.make({})
        old = Entries().planned(spent=EARLIER_DAYS)
        setup.save(old.model_copy(update={"spent_by_day": {OLD_DAY: EARLIER_DAYS}}))
        agent = ScriptedAgent([Step(writes=Files().feature(), cost=CALL_COST), replace(Plans().approved(), cost=CALL_COST)])

        setup.work_on(agent, Items().jira())

        assert setup.entry("ENG-1").status is WorkStatus.BLOCKED


class TestAskOrder:
    """Mutant m12: the cost is booked before the reservation goes, so booked plus reserved never under counts."""

    def test_the_cost_is_booked_before_the_reservation_is_released(self, setups: Setups, monkeypatch: pytest.MonkeyPatch) -> None:
        setup = setups.make({"max_usd_per_day": MIDNIGHT_CAP})
        events: List[str] = []
        real_add, real_release = setup.company.state.add_spend, setup.company.day.release

        async def spy_add(fresh: WorkEntry, day: str, cost: float) -> None:
            events.append("book")
            await real_add(fresh, day, cost)

        def spy_release(grant: Grant) -> None:
            events.append("release")
            real_release(grant)

        monkeypatch.setattr(setup.company.state, "add_spend", spy_add)
        monkeypatch.setattr(setup.company.day, "release", spy_release)

        setup.work_on(ScriptedAgent([Plans().no_code()]), Items().jira())

        assert events == ["book", "release"]


class RaisingAgent:
    async def ask(self, request: AgentRequest) -> AgentAnswer:
        raise AgentCrashed("boom")


class TestSelectorReleases:
    """Mutant m04: the selector gives its reservation back, also when its call raises."""

    def company(self, tmp_path: Path) -> Any:
        return Companies(tmp_path).company(Runbook().two_repos(ROOM_FOR_ONE))

    def test_a_pick_leaves_no_reservation_of_the_day(self, tmp_path: Path) -> None:
        company = self.company(tmp_path)

        asyncio.run(Selector(PickingAgent([]), company).pick([Tickets().plain("ENG-1")], 1, {}))

        assert company.offer(company.settings.max_usd) is not None

    def test_a_selector_call_that_raises_leaves_no_reservation_of_the_day(self, tmp_path: Path) -> None:
        company = self.company(tmp_path)

        with pytest.raises(AgentCrashed, match="boom"):
            asyncio.run(Selector(RaisingAgent(), company).pick([Tickets().plain("ENG-1")], 1, {}))

        assert company.offer(company.settings.max_usd) is not None


class TestFloatResidue:
    """Mutant m15: with no reset when the last call ends, a residue of 5.5e-17 stays held, and two calls that fit the cap
    exactly no longer both get their grant."""

    def test_calls_that_fit_the_cap_exactly_are_granted_after_others_ended(self) -> None:
        cap, booked = DayCap(), Booked(NOTHING)
        small, large = cap.offer(RESIDUE_CAP, booked, SMALL_CALL), cap.offer(RESIDUE_CAP, booked, LARGE_CALL)
        assert small is not None and large is not None
        cap.release(small)
        cap.release(large)

        fits = [cap.offer(RESIDUE_CAP, booked, EXACT_FIT) for _ in range(EXACT_FITS)]

        assert all(grant is not None for grant in fits)

    def test_a_call_in_flight_counts_even_when_its_share_is_lost_in_the_float_sum(self) -> None:
        """Mutant m14: in flight is a count of grants, not `reserved > 0`. A share of 1e-17 next to one of 0.9999999999999999 is lost
        in the sum, so when the big call ends the reserved amount reads 0 with a call still in flight: a third call must still wait
        instead of taking the day left."""
        cap, booked = DayCap(), Booked(NOTHING)
        big, vanishing = cap.offer(WHOLE_CAP, booked, ALMOST_WHOLE), cap.offer(WHOLE_CAP, booked, VANISHING)
        assert big is not None and vanishing is not None
        cap.release(big)

        assert cap.offer(WHOLE_CAP, booked, TICKET_OVER_THE_CAP) is None
