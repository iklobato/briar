"""Decision B10 of `docs/decisions/2026-11-18-serve-spend-caps.md`: a ticket that stopped for money
comes back when the maintainer raises `max_usd` and restarts `serve`, and only then. No network."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

from briar.serve.state import MoneyStop, WorkEntry, WorkSource, WorkStatus
from briar.serve.work import BudgetReopen, ServeCompany, WorkDay
from tests.unit.serve.fakes import Companies

# The `max_usd` the stop happened at (the test runbook), what it is raised to, and what it is
# raised to but still not enough for what the ticket spent.
CAP = 5.0
RAISED = 8.0
SPENT = 5.1
SPENT_MORE_THAN_RAISED = 9.0
PR = 41


class Entries:
    def stopped(self, spent: float, stop: Optional[MoneyStop]) -> WorkEntry:
        return WorkEntry(
            key="ENG-1", source=WorkSource.JIRA, repo="acme/app", title="Add the feature", status=WorkStatus.BLOCKED,
            pr=PR, report="briar stopped: the task spent its max_usd of 5.0", spent_by_day={WorkDay().today(): spent}, money_stop=stop,
        )  # fmt: skip

    def money(self, cap: float = CAP) -> MoneyStop:
        return MoneyStop(cap=cap, resume_to="pr-open", drafted=True)


class Reopened:
    """A company at `max_usd` with `entry` in its state, and what `BudgetReopen` does to it."""

    def __init__(self, tmp_path: Path) -> None:
        self._tmp_path = tmp_path

    def company(self, max_usd: float, entry: WorkEntry) -> ServeCompany:
        company = Companies(self._tmp_path / "knowledge").company({"max_usd": max_usd, "repos": ["acme/app"]})
        asyncio.run(company.state.record(entry))
        return company

    def apply(self, company: ServeCompany) -> List[str]:
        return asyncio.run(BudgetReopen(company).apply())

    def entry(self, company: ServeCompany) -> WorkEntry:
        found = company.state.book().entry("ENG-1")
        assert found is not None
        return found


class TestBudgetReopen:
    def test_a_ticket_comes_back_when_the_cap_is_raised_and_it_has_money(self, tmp_path: Path) -> None:
        reopened = Reopened(tmp_path)
        company = reopened.company(RAISED, Entries().stopped(SPENT, Entries().money()))

        assert reopened.apply(company) == ["ENG-1"]

        entry = reopened.entry(company)
        assert (entry.status, entry.money_stop) == (WorkStatus.PR_OPEN, Entries().money())

    def test_a_ticket_with_money_left_but_no_higher_cap_stays_stopped(self, tmp_path: Path) -> None:
        """The SDK can stop a call for its own budget with the total still under `max_usd`: without
        the cap test that ticket would come back, pay and stop again on every poll."""
        reopened = Reopened(tmp_path)
        company = reopened.company(CAP, Entries().stopped(SPENT - CAP, Entries().money()))

        assert reopened.apply(company) == []
        assert reopened.entry(company).status is WorkStatus.BLOCKED

    def test_a_raised_cap_that_is_still_not_enough_leaves_the_ticket_stopped(self, tmp_path: Path) -> None:
        reopened = Reopened(tmp_path)
        company = reopened.company(RAISED, Entries().stopped(SPENT_MORE_THAN_RAISED, Entries().money()))

        assert reopened.apply(company) == []
        assert reopened.entry(company).status is WorkStatus.BLOCKED

    def test_a_ticket_that_stopped_for_another_reason_is_never_reopened(self, tmp_path: Path) -> None:
        reopened = Reopened(tmp_path)
        company = reopened.company(RAISED, Entries().stopped(SPENT, None))

        assert reopened.apply(company) == []
        assert reopened.entry(company).status is WorkStatus.BLOCKED

    def test_a_ticket_reopened_and_stopped_again_at_the_new_cap_waits_for_a_newer_cap(self, tmp_path: Path) -> None:
        reopened = Reopened(tmp_path)
        company = reopened.company(RAISED, Entries().stopped(SPENT_MORE_THAN_RAISED, Entries().money(cap=RAISED)))

        assert reopened.apply(company) == []
