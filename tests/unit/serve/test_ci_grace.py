"""WP2 of the production plan (P8, P36): how long `serve` waits for a CI that has not started before it decides the repo has none is
`serve.ci_grace_seconds` (default 60, kept; a placeholder, since the first run of CI came 4 and 5 seconds after the PR opened on the only two PRs
whose head did not move). It used to be a constant of the class."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from briar.extract._provider import CiState
from briar.iac.runbook.models import ServeSettings
from briar.serve.state import WorkStatus
from tests.unit.serve.test_settings import Required
from tests.unit.serve.ticket_fakes import Files, Items, Plans, ScriptedAgent, Setups, Step

DEFAULT_GRACE = 60
# Seconds each look at the clock of the flow is later than the one before, and a grace no wait can reach.
STEP = 10
NEVER = 3600
ONE_MINUTE_WAIT = 1


class StepClock:
    """Stands in for `time` inside the flow: every look at the clock is `STEP` seconds later, so no test waits."""

    def __init__(self) -> None:
        self._now = 0.0

    def monotonic(self) -> float:
        self._now += STEP
        return self._now


class Settings:
    def of(self, extra: dict) -> ServeSettings:  # type: ignore[type-arg]
        return ServeSettings.model_validate({**Required().block(), "repos": ["acme/app"], **extra})


class TestTheSetting:
    def test_sixty_seconds_by_default(self) -> None:
        assert Settings().of({}).ci_grace_seconds == DEFAULT_GRACE

    def test_zero_is_allowed_and_means_no_wait(self) -> None:
        assert Settings().of({"ci_grace_seconds": 0}).ci_grace_seconds == 0

    def test_a_negative_wait_stops_the_load(self) -> None:
        with pytest.raises(ValidationError, match=r"ci_grace_seconds\n  Input should be greater than or equal to 0"):
            Settings().of({"ci_grace_seconds": -1})


class TestTheWait:
    """The CI never shows up (`NONE`): the grace decides when briar stops waiting for it."""

    def status_with_grace(self, setups: Setups, monkeypatch: pytest.MonkeyPatch, grace: int) -> WorkStatus:
        monkeypatch.setattr("briar.serve.flow.time", StepClock())
        setup = setups.make({"ci_wait_minutes": ONE_MINUTE_WAIT, "ci_grace_seconds": grace})
        setup.sources.github_side.ci[:] = [CiState.NONE]

        setup.work_on(ScriptedAgent([Plans().plan(), Step(writes=Files().feature()), Plans().approved()]), Items().jira())

        return setup.entry("ENG-1").status

    def test_no_grace_takes_a_repo_with_no_ci_as_done_at_once(self, setups: Setups, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self.status_with_grace(setups, monkeypatch, 0) is WorkStatus.DONE

    def test_a_grace_longer_than_the_wait_keeps_waiting_until_the_wait_ends(self, setups: Setups, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self.status_with_grace(setups, monkeypatch, NEVER) is WorkStatus.BLOCKED
