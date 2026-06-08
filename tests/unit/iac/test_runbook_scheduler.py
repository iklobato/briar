"""RunbookScheduler — registers (company, task) jobs from a directory of
runbook YAMLs and runs the dispatch loop.

No real schedule registry mutation leaks across tests: we point the
`schedule` library at an isolated `schedule.Scheduler()` via the
``scheduler`` argument of EveryParser where relevant, and for
RunbookScheduler we patch the lazy ``load_runbook_file`` / ``extract_runbook``
seams so no extractor or store is touched. The forever-loop is exercised
with a fast tick and a stop() so it never blocks past `timeout`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import schedule as _schedule

from briar.iac.runbook.executor import ExtractRow
from briar.iac.runbook.models import CompanyEntry, ExtractEntry, RunbookFile, ScheduleEntry
from briar.iac.runbook.scheduler import RegisteredJob, RunbookScheduler


@pytest.fixture(autouse=True)
def _isolate_global_schedule(monkeypatch):
    """Each test gets a fresh global schedule registry so jobs registered
    by RunbookScheduler.register_all (which uses the library default) don't
    bleed between tests under pytest-randomly's shuffling."""
    fresh = _schedule.Scheduler()
    monkeypatch.setattr(_schedule, "default_scheduler", fresh)
    yield
    fresh.clear()


def _runbook(schedules_by_company) -> RunbookFile:
    companies = {}
    for name, scheds in schedules_by_company.items():
        companies[name] = CompanyEntry.model_construct(
            schedules=[ScheduleEntry.model_construct(task=t, every=e, extract=[ExtractEntry.model_construct(name="fake", args={})]) for t, e in scheds],
        )
    return RunbookFile.model_construct(version=1, companies=companies)


def _patch_load(mocker, runbook: RunbookFile):
    """Patch both seams the scheduler lazy-imports."""
    import briar.iac.runbook as runbook_pkg

    mocker.patch.object(runbook_pkg, "load_runbook_file", return_value=runbook)
    return mocker.patch.object(runbook_pkg, "extract_runbook", return_value=[])


class TestRegisterAll:
    def test_registers_one_job_per_company_task(self, tmp_path: Path, mocker) -> None:
        (tmp_path / "a.yaml").write_text("ignored — load is mocked")
        runbook = _runbook(
            {
                "acme": [("nightly", "day at 03:17"), ("hourly", "1 hour")],
                "globex": [("nightly", "day at 04:00")],
            }
        )
        _patch_load(mocker, runbook)

        jobs = RunbookScheduler(tmp_path).register_all()
        keys = sorted((j.company, j.task) for j in jobs)
        assert keys == [("acme", "hourly"), ("acme", "nightly"), ("globex", "nightly")]
        assert all(isinstance(j, RegisteredJob) for j in jobs)

    def test_jobs_accessor_returns_a_copy(self, tmp_path: Path, mocker) -> None:
        (tmp_path / "a.yaml").write_text("x")
        _patch_load(mocker, _runbook({"acme": [("nightly", "day at 03:17")]}))
        sched = RunbookScheduler(tmp_path)
        sched.register_all()
        first = sched.jobs()
        first.clear()
        # Mutating the returned list must not empty the scheduler's own list.
        assert len(sched.jobs()) == 1

    def test_no_yaml_files_registers_nothing(self, tmp_path: Path, mocker) -> None:
        # Empty directory → no jobs, no crash. load_runbook_file never called.
        load = mocker.patch("briar.iac.runbook.load_runbook_file")
        jobs = RunbookScheduler(tmp_path).register_all()
        assert jobs == []
        load.assert_not_called()


class TestStagger:
    def test_sub_day_cadence_is_rebased_within_interval(self, tmp_path: Path, mocker) -> None:
        (tmp_path / "a.yaml").write_text("x")
        _patch_load(mocker, _runbook({"acme": [("hourly", "1 hour")]}))
        jobs = RunbookScheduler(tmp_path).register_all()
        job = jobs[0].job
        # A 1-hour cadence staggers 0–59 min: next_run lands strictly within
        # the next hour from now (offset - interval shifts it earlier).
        now = datetime.now()
        assert job.next_run is not None
        assert now - timedelta(hours=1) <= job.next_run <= now + timedelta(hours=1)

    def test_stagger_is_deterministic_per_company_task(self, tmp_path: Path, mocker) -> None:
        from briar.iac.runbook.scheduler import RunbookScheduler as RS

        sched = _schedule.Scheduler()
        job1 = sched.every().hour
        job1.do(lambda: None)
        job2 = sched.every().hour
        job2.do(lambda: None)
        # Pin both to the same base next_run so any difference after stagger
        # is purely the (deterministic) offset, not jitter in when each job
        # was constructed.
        base = datetime(2026, 1, 1, 0, 0, 0)
        job1.next_run = base
        job2.next_run = base
        # Same (company, task) seed → identical offset applied → identical next_run.
        RS._apply_stagger(job1, "acme", "hourly")
        RS._apply_stagger(job2, "acme", "hourly")
        assert job1.next_run == job2.next_run
        # And a different seed yields a different offset (the stagger is keyed).
        job3 = sched.every().hour
        job3.do(lambda: None)
        job3.next_run = base
        RS._apply_stagger(job3, "globex", "hourly")
        assert job3.next_run != job1.next_run

    def test_at_pinned_jobs_are_not_staggered(self) -> None:
        from briar.iac.runbook.scheduler import RunbookScheduler as RS

        sched = _schedule.Scheduler()
        job = sched.every().day.at("03:17")
        job.do(lambda: None)
        before = job.next_run
        RS._apply_stagger(job, "acme", "nightly")
        # .at(...) anchored → left untouched (job.at_time is not None branch).
        assert job.next_run == before

    def test_sub_minute_cadence_not_staggered(self) -> None:
        from briar.iac.runbook.scheduler import RunbookScheduler as RS

        sched = _schedule.Scheduler()
        job = sched.every(30).seconds
        job.do(lambda: None)
        before = job.next_run
        RS._apply_stagger(job, "acme", "fast")
        # cadence < 60s → early return, no rebase.
        assert job.next_run == before


class TestJobCallable:
    def test_fired_job_runs_extract_and_logs_results(self, tmp_path: Path, mocker, caplog_briar) -> None:
        (tmp_path / "a.yaml").write_text("x")
        extract = _patch_load(mocker, _runbook({"acme": [("nightly", "day at 03:17")]}))
        extract.return_value = [ExtractRow("acme", "nightly", "wrote 5 bytes via store=file", "acme.md")]

        sched = RunbookScheduler(tmp_path)
        jobs = sched.register_all()
        # Invoke the registered closure directly (job.job_func is the callable).
        jobs[0].job.job_func()

        # extract_runbook called with the registered task.
        assert extract.call_args.args[1] == "nightly"
        joined = "\n".join(r.message for r in caplog_briar.records)
        assert "wrote 5 bytes" in joined

    def test_fired_job_survives_extract_exception(self, tmp_path: Path, mocker, caplog_briar) -> None:
        (tmp_path / "a.yaml").write_text("x")
        mocker.patch("briar.iac.runbook.load_runbook_file", side_effect=RuntimeError("boom in load"))

        # _make_callable closes over its own lazy import, so register_all needs
        # a runbook to register the job; patch register-time load separately.
        sched = RunbookScheduler(tmp_path)
        # Build the callable directly — register_all's load is independent.
        job_fn = sched._make_callable(tmp_path / "a.yaml", "acme", "nightly")
        job_fn()  # must NOT raise — the loop must survive a misbehaving job.
        assert any("FAILED" in r.message for r in caplog_briar.records)


class TestRunForever:
    def test_stop_event_breaks_the_loop(self, tmp_path: Path) -> None:
        import threading
        import time

        sched = RunbookScheduler(tmp_path)
        thread = threading.Thread(target=sched.run_forever, kwargs={"tick_seconds": 0.01})
        thread.start()
        # Give the loop a moment to enter, then stop it.
        time.sleep(0.05)
        sched.stop()
        thread.join(timeout=5)
        assert not thread.is_alive()

    def test_run_pending_exception_does_not_kill_loop(self, tmp_path: Path, mocker, caplog_briar) -> None:
        import threading
        import time

        # First run_pending raises; loop must log and keep going until stop().
        calls = {"n": 0}

        def _flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient scheduler fault")

        mocker.patch("schedule.run_pending", side_effect=_flaky)
        sched = RunbookScheduler(tmp_path)
        thread = threading.Thread(target=sched.run_forever, kwargs={"tick_seconds": 0.01})
        thread.start()
        time.sleep(0.08)
        sched.stop()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert calls["n"] >= 2  # survived the first raise and ticked again
        assert any("run_pending() raised" in r.message for r in caplog_briar.records)
