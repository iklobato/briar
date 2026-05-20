"""In-process scheduler — replaces cron.

`EveryParser` turns the YAML's tiny `every: "<expr>"` DSL into a
`schedule.Job` instance. `RunbookScheduler` walks every company × task
in a directory, registers each as a `schedule` job, and runs the
forever-loop. The dashboard's `SchedulesCollector` re-uses the parser
to compute the next-fire time per task without sharing process state.

Pattern grammar (case-insensitive):
    <interval>              "minute" | "hour" | "day" | "monday" | ...
    <n> <interval>          "10 minutes" | "4 hours" | "2 days"
    <interval> at <HH:MM>   "day at 03:17" | "monday at 09:00"
    <interval> at :<MM>     "hour at :15"
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

import schedule

from briar.errors import ConfigError
from briar.iac.runbook.models import RunbookFile, ScheduleEntry


_WEEKDAYS = {
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
}
_PLURAL_TO_SINGULAR = {
    "minutes": "minute", "hours": "hour", "days": "day", "weeks": "week",
}
_PATTERN = re.compile(
    r"""^
    (?:(?P<count>\d+)\s+)?
    (?P<unit>minute|minutes|hour|hours|day|days|week|weeks
            |monday|tuesday|wednesday|thursday|friday|saturday|sunday)
    (?:\s+at\s+(?P<at>:?\d{1,2}(?::\d{2})?))?
    $""",
    re.IGNORECASE | re.VERBOSE,
)


class EveryParser:
    """Translate the YAML `every:` string into a configured
    `schedule.Job` (without `.do(...)` yet — caller binds the callable)."""

    DEFAULT_TZ = "UTC"

    @classmethod
    def parse(
        cls,
        expr: str,
        *,
        tz: str = DEFAULT_TZ,
        scheduler: Optional[schedule.Scheduler] = None,
    ) -> schedule.Job:
        """Parse `expr` into a Job. `scheduler` defaults to the library's
        global registry; pass a private `schedule.Scheduler()` to keep
        jobs isolated (the dashboard reads next-fire without polluting
        the real scheduler's state)."""
        match = _PATTERN.match(expr.strip().lower())
        if match is None:
            raise ConfigError(
                f"every: cannot parse {expr!r} — try things like "
                f"'day at 03:17', '4 hours', 'hour at :15'"
            )
        count = int(match.group("count")) if match.group("count") else 1
        unit = match.group("unit")
        if unit in _PLURAL_TO_SINGULAR:
            singular = _PLURAL_TO_SINGULAR[unit]
        else:
            singular = unit

        if scheduler is None:
            job: schedule.Job = (
                schedule.every(count) if count > 1 else schedule.every()
            )
        else:
            job = (
                scheduler.every(count) if count > 1 else scheduler.every()
            )

        # Map the unit onto the schedule fluent API. Dispatch table —
        # singular form gets `.minute / .hour / ...`, plural via count.
        unit_attr = singular if count == 1 else f"{singular}s"
        # schedule's Job uses singular when count==1, plural when count>1.
        # `schedule.every().minute` vs `schedule.every(5).minutes`.
        # The weekday names are only valid when count==1.
        if singular in _WEEKDAYS and count != 1:
            raise ConfigError(
                f"every: weekday {singular!r} cannot have a count "
                f"(got {count}); use 'every {singular}' without a number"
            )
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
        self._jobs: List[Tuple[str, str, schedule.Job]] = []
        self._stop_event = threading.Event()

    def register_all(self) -> List[Tuple[str, str, str]]:
        """Walk YAMLs + register one job per (company, task). Returns
        rows of (company, task, every) for surface visibility."""
        from briar.iac.runbook.executor import RunbookSchedules
        from briar.iac.runbook import load_runbook_file

        registered: List[Tuple[str, str, str]] = []
        for path in sorted(self._dir.glob("*.yaml")):
            runbook = load_runbook_file(path)
            for company_name, company in runbook.companies.items():
                for entry in RunbookSchedules.for_company(company):
                    job = EveryParser.parse(entry.every)
                    job.do(self._make_callable(path, company_name, entry.task))
                    self._jobs.append((company_name, entry.task, job))
                    registered.append((company_name, entry.task, entry.every))
        return registered

    def _make_callable(self, yaml_path: Path, company: str, task: str):
        """Return a no-arg closure suitable for `schedule.Job.do(...)`.
        Lazy-imports so the schedule module can be loaded without
        boto3 / extractors paying the price."""

        def _job():
            from briar.iac.runbook import extract_runbook, load_runbook_file
            print(f"[scheduler] task={task} company={company} fire")
            try:
                runbook = load_runbook_file(yaml_path)
                rows = extract_runbook(runbook, task=task)
                for c, t, status, output in rows:
                    print(f"[scheduler] {c} {t}: {status} -> {output}")
            except Exception as exc:  # noqa: BLE001 — must not abort the loop
                print(f"[scheduler] task={task} company={company} FAILED: {exc}")

        return _job

    def jobs(self) -> List[Tuple[str, str, schedule.Job]]:
        return list(self._jobs)

    def run_forever(self, *, tick_seconds: float = 1.0) -> None:
        """Block + dispatch jobs until `stop()` or KeyboardInterrupt."""
        print(
            f"[scheduler] registered {len(self._jobs)} job(s); "
            "entering run loop (Ctrl-C to stop)"
        )
        while not self._stop_event.is_set():
            schedule.run_pending()
            self._stop_event.wait(tick_seconds)

    def stop(self) -> None:
        self._stop_event.set()
