"""In-process scheduler — replaces cron.

`EveryParser` turns the YAML's tiny `every:` DSL into a `schedule.Job`
instance. `RunbookScheduler` walks every company × task in a directory,
registers each as a `schedule` job, and runs the forever-loop.

Pattern grammar (case-insensitive):
    <interval>              "minute" | "hour" | "day" | "monday" | ...
    <n> <interval>          "10 minutes" | "4 hours" | "2 days"
    <interval> at <HH:MM>   "day at 03:17" | "monday at 09:00"
    <interval> at :<MM>     "hour at :15"
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List

import schedule

from briar.errors import ConfigError


log = logging.getLogger(__name__)


_WEEKDAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
_PLURAL_TO_SINGULAR = {"minutes": "minute", "hours": "hour", "days": "day", "weeks": "week"}
_PATTERN = re.compile(
    r"""^
    (?:(?P<count>\d+)\s+)?
    (?P<unit>minute|minutes|hour|hours|day|days|week|weeks
            |monday|tuesday|wednesday|thursday|friday|saturday|sunday)
    (?:\s+at\s+(?P<at>:?\d{1,2}(?::\d{2})?))?
    $""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class RegisteredJob:
    """One registered (company, task, every, job) row."""

    company: str
    task: str
    every: str
    job: schedule.Job


class EveryParser:
    """Translate the YAML `every:` string into a configured `schedule.Job`.

    Callers may pass a `scheduler` to register the job into a private
    `schedule.Scheduler()` instead of the library's global registry."""

    DEFAULT_TZ = "UTC"
    # Sentinel: the library exposes `schedule.default_scheduler` as the
    # process-wide default. `_NO_SCHEDULER` is the "use default" marker
    # so we never thread an Optional through the API.
    _GLOBAL_SCHEDULER: schedule.Scheduler = schedule.default_scheduler

    @classmethod
    def parse(cls, expr: str, tz: str = DEFAULT_TZ, scheduler: schedule.Scheduler = _GLOBAL_SCHEDULER) -> schedule.Job:
        match = _PATTERN.match(expr.strip().lower())
        if match is None:
            raise ConfigError(f"every: cannot parse {expr!r} — try things like 'day at 03:17', '4 hours', 'hour at :15'")
        count = int(match.group("count")) if match.group("count") else 1
        unit = match.group("unit")
        singular = _PLURAL_TO_SINGULAR.get(unit, unit)

        job: schedule.Job = scheduler.every(count) if count > 1 else scheduler.every()

        unit_attr = singular if count == 1 else f"{singular}s"
        if singular in _WEEKDAYS and count != 1:
            raise ConfigError(f"every: weekday {singular!r} cannot have a count (got {count}); use 'every {singular}' without a number")
        job = vars(job.__class__)[unit_attr].fget(job)

        at = match.group("at")
        if at:
            job = job.at(at, tz) if singular in {"day", *_WEEKDAYS} else job.at(at)
        return job


class RunbookScheduler:
    """Long-lived scheduler — registers every (company, task) in a
    directory of YAMLs and runs the schedule loop."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._jobs: List[RegisteredJob] = []
        self._stop_event = threading.Event()

    def register_all(self) -> List[RegisteredJob]:
        """Walk YAMLs + register one job per (company, task)."""
        from briar.iac.runbook import load_runbook_file
        from briar.iac.runbook.executor import RunbookSchedules

        for path in sorted(self._dir.glob("*.yaml")):
            runbook = load_runbook_file(path)
            for company_name, company in runbook.companies.items():
                for entry in RunbookSchedules.for_company(company):
                    job = EveryParser.parse(entry.every)
                    job.do(self._make_callable(path, company_name, entry.task))
                    self._jobs.append(RegisteredJob(company_name, entry.task, entry.every, job))
        return list(self._jobs)

    def _make_callable(self, yaml_path: Path, company: str, task: str):
        """Return a no-arg closure suitable for `schedule.Job.do(...)`."""

        def _job() -> None:
            from briar.iac.runbook import extract_runbook, load_runbook_file

            log.info("fire task=%s company=%s yaml=%s", task, company, yaml_path.name)
            try:
                runbook = load_runbook_file(yaml_path)
                rows = extract_runbook(runbook, task)
                for row in rows:
                    log.info("result task=%s company=%s status=%s output=%s", row.task, row.company, row.status, row.output)
            except Exception:  # noqa: BLE001 — must not abort the loop
                # logger.exception() includes the full traceback in the log.
                log.exception("FAILED task=%s company=%s yaml=%s", task, company, yaml_path.name)

        return _job

    def jobs(self) -> List[RegisteredJob]:
        return list(self._jobs)

    def run_forever(self, tick_seconds: float = 1.0) -> None:
        """Block + dispatch jobs until `stop()` or KeyboardInterrupt."""
        log.info("scheduler starting: %d job(s), tick=%.1fs (Ctrl-C to stop)", len(self._jobs), tick_seconds)
        for entry in self._jobs:
            log.debug("scheduled job: company=%s task=%s every=%r next_run=%s", entry.company, entry.task, entry.every, entry.job.next_run)
        while not self._stop_event.is_set():
            try:
                schedule.run_pending()
            except Exception:  # noqa: BLE001 — survive a misbehaving job
                log.exception("schedule.run_pending() raised; loop continues")
            self._stop_event.wait(tick_seconds)
        log.info("scheduler stopped (stop_event set)")

    def stop(self) -> None:
        self._stop_event.set()
