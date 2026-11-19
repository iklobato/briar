"""WP1 of the production plan (P13, P31): a refused login stops the WHOLE `serve`. The poll that meets a dead login asks for the
stop, no company polls again, the tasks that run get `call_minutes` to finish and the rest are cancelled, and the process
exits with the code 3. A poll that fails for any other reason is logged and tried again, as before."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import pytest

from briar.auth.check import LoginRejected
from briar.commands._enums import ExitCode
from briar.extract._tracker import Ticket
from briar.iac.runbook.models import ServeSettings
from briar.serve.loop import CompanyWatch, ServeLoop, TaskSlots, WatchWork
from briar.serve.pause import DeadLogin
from briar.serve.work import ServeCompanies, ServeCompany
from tests.unit.serve.fakes import Companies, FakeJira, FakeSources
from tests.unit.serve.test_loop import CountingRoutine, Items, Watching

UNAUTHORIZED = 401
SERVICE_UNAVAILABLE = 503
TWO_SLOTS = 2
# A call limit the tests use instead of minutes: what a running task gets to finish, and a short one for the task that never does.
ROOMY = 5.0
SHORT = 0.05
# When the held task is let go, inside `ROOMY`.
LET_GO_AFTER = 0.05
LOGIN_EXIT_CODE = 3


class Dead:
    """The logins that died, as `LoginWatch` reports them."""

    def jira(self) -> DeadLogin:
        return DeadLogin("jira", "acme", "HTTP 401: Unauthorized", "renew the cookie")

    def github(self) -> DeadLogin:
        return DeadLogin("github", "acme", "HTTP 401: Bad credentials", "renew the token")


class Answers:
    """What the Jira of a test says: the poll and "who am I" both with the same HTTP status."""

    def refused(self, status: int) -> LoginRejected:
        return LoginRejected.at(f"HTTP {status}: no", status)


@dataclass(frozen=True)
class DeadJira(FakeJira):
    """A Jira whose login died: the poll and "who am I" are refused with a 401."""

    def who_am_i(self) -> str:
        raise Answers().refused(UNAUTHORIZED)

    def assigned(self, project: str, max_count: int) -> List[Ticket]:
        self.calls.append(project)
        raise Answers().refused(UNAUTHORIZED)


@dataclass(frozen=True)
class DownJira(FakeJira):
    """A Jira that is down: the poll gets a 503, "who am I" still works."""

    def assigned(self, project: str, max_count: int) -> List[Ticket]:
        self.calls.append(project)
        raise Answers().refused(SERVICE_UNAVAILABLE)


class Scheduler:
    """The extract scheduler of a test: nothing is scheduled."""

    def tick(self) -> None:
        return None


class BrokenScheduler:
    """An extract scheduler that fails at its first tick."""

    def tick(self) -> None:
        raise ZeroDivisionError("the schedule is broken")


class Folder:
    def company(self, tmp_path: Path) -> ServeCompany:
        return Companies(tmp_path / "knowledge").company({"jira": {"ENG": {"repos": ["acme/app"]}}})

    def watch(self, company: ServeCompany, routine: CountingRoutine) -> Tuple[CompanyWatch, TaskSlots]:
        slots = TaskSlots(asyncio.Semaphore(TWO_SLOTS), set(), set())
        return CompanyWatch(company, slots, WatchWork(routine, Watching(company).work().choice)), slots

    def loop(self, company: ServeCompany, watch: CompanyWatch) -> ServeLoop:
        return ServeLoop([watch], Scheduler(), company.stop)


SERVE_BLOCK = "    serve:\n      model: claude-test\n      max_usd: 5\n      max_loops: 3\n      ci_wait_minutes: 30\n      max_review_rounds: 2\n      repos: [acme/app]\n"


class TestSharedBetweenCompanies:
    def test_the_companies_of_a_folder_share_the_request_to_stop_and_the_plan_gate(self, tmp_path: Path) -> None:
        """One process and one plan: a login refused for one company must reach every watch, and a limit every company."""
        (tmp_path / "all.yaml").write_text(f"version: 1\ncompanies:\n  acme:\n{SERVE_BLOCK}  beta:\n{SERVE_BLOCK}")

        acme, beta = ServeCompanies(tmp_path, {}).load()

        assert (acme.stop is beta.stop, acme.gate is beta.gate) == (True, True)


class TestPollWithADeadLogin:
    def test_a_poll_that_fails_with_a_dead_login_asks_serve_to_stop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources(jira_adapter=DeadJira()))
        company = Folder().company(tmp_path)
        watch, _ = Folder().watch(company, CountingRoutine(asyncio.Event()))

        started = asyncio.run(watch.cycle())

        dead = company.stop.reason()
        assert (started, company.stop.asked()) == (0, True)
        assert dead is not None
        assert (dead.source, dead.company, dead.detail) == ("jira", "acme", "HTTP 401: no")

    def test_a_poll_that_fails_for_another_reason_is_tried_again_and_serve_goes_on(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jira = DownJira()
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources(jira_adapter=jira))
        company = Folder().company(tmp_path)
        watch, _ = Folder().watch(company, CountingRoutine(asyncio.Event()))

        async def scenario() -> List[int]:
            return [await watch.cycle(), await watch.cycle()]

        assert (asyncio.run(scenario()), company.stop.asked(), jira.calls) == ([0, 0], False, ["ENG", "ENG"])

    def test_no_poll_runs_once_serve_was_asked_to_stop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jira = FakeJira()
        monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources(jira_adapter=jira))
        company = Folder().company(tmp_path)
        company.stop.request(Dead().jira())

        started = asyncio.run(Folder().watch(company, CountingRoutine(asyncio.Event()))[0].cycle())

        assert (started, jira.calls) == (0, [])


class TestTheStop:
    @pytest.fixture
    def roomy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ServeSettings, "call_seconds", lambda settings: ROOMY)

    @pytest.fixture
    def short(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ServeSettings, "call_seconds", lambda settings: SHORT)

    def test_a_task_that_runs_gets_call_minutes_to_finish(self, tmp_path: Path, roomy: None) -> None:
        company, routine = Folder().company(tmp_path), CountingRoutine(asyncio.Event())
        watch, slots = Folder().watch(company, routine)
        loop = Folder().loop(company, watch)

        async def scenario() -> None:
            slots.dispatch(Items().ticket("ENG-1"), routine)
            await asyncio.sleep(0)
            company.stop.request(Dead().jira())
            asyncio.get_running_loop().call_later(LET_GO_AFTER, routine.release.set)
            await loop.serve_until_stopped()

        asyncio.run(scenario())

        assert (routine.started, routine.running[0], loop.exit_code()) == (["ENG-1"], 0, ExitCode.LOGIN_REFUSED)

    def test_a_task_that_does_not_finish_in_time_is_cancelled(self, tmp_path: Path, short: None) -> None:
        company, routine = Folder().company(tmp_path), CountingRoutine(asyncio.Event())
        watch, slots = Folder().watch(company, routine)
        loop = Folder().loop(company, watch)

        async def scenario() -> int:
            slots.dispatch(Items().ticket("ENG-1"), routine)
            await asyncio.sleep(0)
            company.stop.request(Dead().jira())
            await loop.serve_until_stopped()
            return slots.busy()

        assert (asyncio.run(scenario()), routine.running[0]) == (0, 1)

    def test_the_exit_code_is_3_only_when_serve_was_asked_to_stop(self, tmp_path: Path) -> None:
        company = Folder().company(tmp_path)
        loop = Folder().loop(company, Folder().watch(company, CountingRoutine(asyncio.Event()))[0])
        before = loop.exit_code()

        company.stop.request(Dead().jira())

        assert (before, loop.exit_code(), int(ExitCode.LOGIN_REFUSED)) == (ExitCode.OK, ExitCode.LOGIN_REFUSED, LOGIN_EXIT_CODE)

    def test_a_loop_that_crashes_ends_serve_with_its_error(self, tmp_path: Path) -> None:
        """What `gather` did: an error of the extract tick or of a poll is not swallowed by the stop logic."""
        company = Folder().company(tmp_path)
        watch, _ = Folder().watch(company, CountingRoutine(asyncio.Event()))

        with pytest.raises(ZeroDivisionError):
            asyncio.run(ServeLoop([watch], BrokenScheduler(), company.stop).serve_until_stopped())

    def test_the_first_reason_is_the_one_kept(self, tmp_path: Path) -> None:
        company = Folder().company(tmp_path)

        first_asked, second_asked = company.stop.request(Dead().jira()), company.stop.request(Dead().github())

        assert (first_asked, second_asked, company.stop.reason()) == (True, False, Dead().jira())
