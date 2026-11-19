"""WP1 of the production plan (P16, P30): the status file of `serve`, `<knowledge>/work/<company>.status.json`. It is written at the end of
every poll, so an old time shows that `serve` is gone (a kill, a reboot, a crash leave no other trace); it says `stopping` as soon as a
refused login asks for the stop and `stopped` when the running tasks are done, with the reason, the source and the fix. A write that
fails is logged and never stops `serve`."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict

import pytest
import time_machine

from briar.iac.runbook.models import ServeSettings
from briar.serve.pause import DeadLogin
from briar.serve.work import ServeCompany
from tests.unit.serve.fakes import FakeSources
from tests.unit.serve.test_loop import CountingRoutine, Items
from tests.unit.serve.test_stop import Folder

LOGIN_EXIT_CODE = 3
ROOMY = 5.0
# Long enough for the loop to see the stop and write `stopping`, short next to `ROOMY`.
SETTLE = 0.1
NOON = dt.datetime(2030, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
LATER = dt.datetime(2030, 1, 1, 12, 5, 30, tzinfo=dt.timezone.utc)
NOON_TEXT = "2030-01-01T12:00:00+00:00"
LATER_TEXT = "2030-01-01T12:05:30+00:00"


class Dead:
    def jira(self) -> DeadLogin:
        return DeadLogin("jira", "acme", "HTTP 401: Unauthorized", "renew the cookie")


class Status:
    """The status file of a company, read back."""

    def path(self, company: ServeCompany) -> Path:
        return company.root / "work" / "acme.status.json"

    def read(self, company: ServeCompany) -> Dict[str, Any]:
        record: Dict[str, Any] = json.loads(self.path(company).read_text())
        return record


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("briar.serve.loop.WorkSources", lambda logins, kind: FakeSources())


class TestHeartbeat:
    def test_a_poll_leaves_the_state_the_pid_and_the_time_of_the_poll(self, tmp_path: Path, quiet: None) -> None:
        company = Folder().company(tmp_path)
        watch, _ = Folder().watch(company, CountingRoutine(asyncio.Event()))

        with time_machine.travel(NOON, tick=False):
            asyncio.run(watch.cycle())

        assert Status().read(company) == {"state": "running", "company": "acme", "pid": os.getpid(), "updated_at": NOON_TEXT, "stop": None}

    def test_the_next_poll_moves_the_time(self, tmp_path: Path, quiet: None) -> None:
        company = Folder().company(tmp_path)
        watch, _ = Folder().watch(company, CountingRoutine(asyncio.Event()))
        with time_machine.travel(NOON, tick=False):
            asyncio.run(watch.cycle())

        with time_machine.travel(LATER, tick=False):
            asyncio.run(watch.cycle())

        assert Status().read(company)["updated_at"] == LATER_TEXT

    def test_a_poll_after_the_stop_was_asked_does_not_say_running_again(self, tmp_path: Path, quiet: None) -> None:
        company = Folder().company(tmp_path)
        watch, _ = Folder().watch(company, CountingRoutine(asyncio.Event()))
        asyncio.run(watch.cycle())
        company.stop.request(Dead().jira())
        asyncio.run(watch.settle())

        asyncio.run(watch.cycle())

        assert Status().read(company)["state"] == "stopped"

    def test_a_restart_replaces_the_record_of_an_earlier_stop(self, tmp_path: Path, quiet: None) -> None:
        company = Folder().company(tmp_path)
        watch, _ = Folder().watch(company, CountingRoutine(asyncio.Event()))
        company.stop.request(Dead().jira())
        asyncio.run(watch.settle())
        restarted = Folder().company(tmp_path)

        asyncio.run(Folder().watch(restarted, CountingRoutine(asyncio.Event()))[0].cycle())

        assert Status().read(restarted)["state"] == "running"
        assert Status().read(restarted)["stop"] is None

    @pytest.mark.usefixtures("quiet")
    def test_a_write_that_fails_is_logged_and_does_not_stop_the_poll(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """The folder of the file is a plain file here, so the write fails the way a full or read-only disk does."""
        company = Folder().company(tmp_path)
        company.root.mkdir(parents=True, exist_ok=True)
        (company.root / "work").write_text("not a folder")
        watch, _ = Folder().watch(company, CountingRoutine(asyncio.Event()))

        started = asyncio.run(watch.cycle())

        assert (started, Status().path(company).exists()) == (0, False)
        assert "serve: acme: could not write the status file" in caplog.text


class TestTheStopRecord:
    @pytest.fixture
    def roomy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ServeSettings, "call_seconds", lambda settings: ROOMY)

    def test_a_stop_leaves_the_reason_the_source_the_company_and_the_fix(self, tmp_path: Path, roomy: None) -> None:
        company = Folder().company(tmp_path)
        loop = Folder().loop(company, Folder().watch(company, CountingRoutine(asyncio.Event()))[0])
        company.stop.request(Dead().jira())

        with time_machine.travel(NOON, tick=False):
            asyncio.run(loop.serve_until_stopped())

        stop = {"source": "jira", "company": "acme", "detail": "HTTP 401: Unauthorized", "fix": "renew the cookie", "exit_code": LOGIN_EXIT_CODE, "at": NOON_TEXT}
        assert Status().read(company) == {"state": "stopped", "company": "acme", "pid": os.getpid(), "updated_at": NOON_TEXT, "stop": stop}

    def test_the_file_says_stopping_while_the_running_tasks_finish(self, tmp_path: Path, roomy: None) -> None:
        company, routine = Folder().company(tmp_path), CountingRoutine(asyncio.Event())
        watch, slots = Folder().watch(company, routine)
        loop = Folder().loop(company, watch)
        seen: Dict[str, Any] = {}

        async def scenario() -> None:
            slots.dispatch(Items().ticket("ENG-1"), routine)
            await asyncio.sleep(0)
            company.stop.request(Dead().jira())
            running = asyncio.create_task(loop.serve_until_stopped())
            await asyncio.sleep(SETTLE)
            seen.update(Status().read(company))
            routine.release.set()
            await running

        asyncio.run(scenario())

        assert (seen["state"], seen["stop"]["source"], Status().read(company)["state"]) == ("stopping", "jira", "stopped")
