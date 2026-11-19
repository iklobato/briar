"""WP3 of the production plan (P6; finding 16 of the audit): money is added one booking at a time, and floats do not add up exactly: ten bookings
of 0.1 give 0.9999999999999999. Against a cap of 1.0 the limit checks (`left <= 0`) saw a hair of room and made a call with a grant of about 1e-16,
which the SDK stops at its first turn. A hair of money is no room: `DUST` is the least that counts."""

from __future__ import annotations

from pathlib import Path

import pytest

from briar.serve.agent import AgentAnswer
from briar.serve.day import DayCap, DayPause
from briar.serve.task import Budget, BudgetExhausted
from briar.serve.work import WorkDay
from tests.unit.serve.fakes import Companies
from tests.unit.serve.test_day_cap import Elsewhere

TEN = 10
BOOKING = 0.1
WHOLE = 1.0
LOOPS = 3
# A day with a tenth of a cent left: small, but real room.
BOOKED_A_HAIR_UNDER = 0.999
TICKET_LEFT = 5.0


class Booked:
    """The spend of ten bookings of 0.1, added one by one as the state adds them."""

    def total(self) -> float:
        spent = 0.0
        for _ in range(TEN):
            spent += BOOKING
        return spent


class TestFloatDust:
    def test_the_residue_is_a_real_residue(self) -> None:
        """The premise of the tests: the sum is not 1.0."""
        assert Booked().total() != WHOLE

    def test_a_ticket_with_a_hair_left_has_nothing_left(self) -> None:
        budget = Budget(WHOLE, LOOPS)
        for _ in range(TEN):
            budget.add(BOOKING)

        with pytest.raises(BudgetExhausted):
            budget.require()

    def test_a_call_that_leaves_a_hair_of_the_ticket_stops_it(self) -> None:
        budget = Budget(WHOLE, LOOPS)
        for _ in range(TEN):
            budget.add(BOOKING)

        with pytest.raises(BudgetExhausted):
            budget.check(AgentAnswer("", {}, 0.0))

    def test_a_ticket_with_real_money_left_still_runs(self) -> None:
        budget = Budget(WHOLE, LOOPS)
        budget.add(BOOKED_A_HAIR_UNDER)

        budget.require()

        assert budget.left_usd() == pytest.approx(WHOLE - BOOKED_A_HAIR_UNDER)

    def test_a_day_with_a_hair_left_is_at_its_cap(self) -> None:
        with pytest.raises(DayPause):
            DayCap().offer(WHOLE, Booked().total, TICKET_LEFT)

    def test_a_day_with_real_money_left_gives_it_as_the_grant(self) -> None:
        grant = DayCap().offer(WHOLE, lambda: BOOKED_A_HAIR_UNDER, TICKET_LEFT)

        assert grant is not None
        assert grant.amount == pytest.approx(WHOLE - BOOKED_A_HAIR_UNDER)

    def test_a_company_with_a_hair_left_of_its_day_is_at_the_cap(self, tmp_path: Path) -> None:
        """The gate of the poll: ten bookings of 0.1 against `max_usd_per_day` 1.0 must stop the dispatch of model work."""
        company = Companies(tmp_path / "knowledge").company({"repos": ["acme/app"], "max_usd_per_day": WHOLE})
        for _ in range(TEN):
            Elsewhere().spend(company, BOOKING, WorkDay().today())

        assert company.capped() is True

    def test_a_company_with_real_money_left_of_its_day_is_not_at_the_cap(self, tmp_path: Path) -> None:
        company = Companies(tmp_path / "knowledge").company({"repos": ["acme/app"], "max_usd_per_day": WHOLE})
        Elsewhere().spend(company, BOOKED_A_HAIR_UNDER, WorkDay().today())

        assert company.capped() is False
