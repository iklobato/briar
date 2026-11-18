"""A model call that never answers (hardening PR, audit 9): it used to keep its reservation of the day for ever, so later
calls waited for ever, and a selector awaited inside the poll froze the polling of the whole company. Now every call has
a limit in minutes, `call_minutes` of the runbook."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List

import pytest
from pydantic import ValidationError

from briar.iac.runbook.models import ServeSettings
from briar.serve.agent import AgentAnswer, AgentRequest
from briar.serve.select import Selector
from briar.serve.state import WorkStatus
from tests.unit.serve.fakes import Companies
from tests.unit.serve.test_select import Tickets
from tests.unit.serve.test_settings import Required
from tests.unit.serve.ticket_fakes import Items, Setups

DEFAULT_CALL_MINUTES = 30
SET_CALL_MINUTES = 45
SECONDS_PER_MINUTE = 60
# What the tests use instead of minutes: a hung call is cut after this many seconds.
CUT_AFTER = 0.05
DAY_CAP = 10.0
REPORT = f"the agent did not answer within call_minutes ({DEFAULT_CALL_MINUTES})"


class HungAgent:
    """A model call that never returns."""

    async def ask(self, request: AgentRequest) -> AgentAnswer:
        return await asyncio.get_running_loop().create_future()


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    """A call limit of `CUT_AFTER` seconds, whatever `call_minutes` says, so no test waits for a minute."""
    monkeypatch.setattr(ServeSettings, "call_seconds", lambda settings: CUT_AFTER)


class TestCallMinutes:
    def settings(self, extra: dict) -> ServeSettings:  # type: ignore[type-arg]
        return ServeSettings.model_validate({**Required().block(), "repos": ["acme/app"], **extra})

    def test_thirty_minutes_by_default(self) -> None:
        settings = self.settings({})

        assert (settings.call_minutes, settings.call_seconds()) == (DEFAULT_CALL_MINUTES, DEFAULT_CALL_MINUTES * SECONDS_PER_MINUTE)

    def test_the_runbook_sets_it_in_minutes(self) -> None:
        assert self.settings({"call_minutes": SET_CALL_MINUTES}).call_seconds() == SET_CALL_MINUTES * SECONDS_PER_MINUTE

    def test_zero_minutes_stop_the_load(self) -> None:
        with pytest.raises(ValidationError, match=r"call_minutes\n  Input should be greater than or equal to 1"):
            self.settings({"call_minutes": 0})


@pytest.mark.usefixtures("quick")
class TestAHungTaskCall:
    def test_the_task_is_blocked_with_the_reason(self, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(HungAgent(), Items().jira())  # type: ignore[arg-type]

        entry = setup.entry("ENG-1")
        assert (entry.status, REPORT in entry.report) == (WorkStatus.BLOCKED, True)

    def test_the_reservation_of_the_day_is_given_back(self, setups: Setups) -> None:
        setup = setups.make({"max_usd_per_day": DAY_CAP})

        setup.work_on(HungAgent(), Items().jira())  # type: ignore[arg-type]

        assert setup.company.offer(setup.company.settings.max_usd) is not None

    def test_a_cost_nobody_knows_is_not_booked(self, setups: Setups) -> None:
        setup = setups.make({})

        setup.work_on(HungAgent(), Items().jira())  # type: ignore[arg-type]

        assert setup.entry("ENG-1").spent_total() == 0.0


@pytest.mark.usefixtures("quick")
class TestAHungSelectorCall:
    def pick(self, tmp_path: Path) -> List[object]:
        company = Companies(tmp_path).company({"jira": {"ENG": {"repos": ["acme/app", "acme/api"]}}, "max_usd_per_day": DAY_CAP})
        picked = asyncio.run(Selector(HungAgent(), company).pick([Tickets().plain("ENG-1")], 1, {}))  # type: ignore[arg-type]
        assert company.offer(company.settings.max_usd) is not None
        return list(picked or [])

    def test_the_poll_goes_on_with_no_pick_and_the_reservation_is_given_back(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        assert self.pick(tmp_path) == []
        assert "the selector failed" in caplog.text
