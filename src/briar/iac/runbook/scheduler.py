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

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import List, Optional

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

    @classmethod
    def parse(cls, expr: str, tz: str = DEFAULT_TZ, scheduler: Optional[schedule.Scheduler] = None) -> schedule.Job:
        # Resolve at call time, not at import — a test or caller that
        # swaps `schedule.default_scheduler` after we import sees the
        # change. The previous shape snapshotted at import time.
        scheduler = scheduler if scheduler is not None else schedule.default_scheduler

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
        # Public property access instead of `vars(job.__class__)[name].fget(job)`
        # — same behavior, but resilient to library refactors that move
        # the property to a different class in the MRO.
        job = getattr(job, unit_attr)

        at = match.group("at")
        if at:
            if singular in {"day", *_WEEKDAYS}:
                job = job.at(at, tz)
            else:
                # hour/minute units silently drop tz in schedule — warn so
                # the operator doesn't think their non-UTC tz applies to
                # sub-day cadences. The `schedule` library doesn't support
                # tz-aware sub-day scheduling, so a DST shift means the
                # job will fire at the same wall-clock UTC time across
                # the transition. Documented at runbook scheduler level.
                if tz != cls.DEFAULT_TZ:
                    log.warning(
                        "every: tz=%r is ignored for sub-day cadence %r — schedule's wall-clock support is day-or-coarser only",
                        tz,
                        singular,
                    )
                job = job.at(at)
        return job


class RunbookScheduler:
    """Long-lived scheduler — registers every (company, task) in a
    directory of YAMLs and runs the schedule loop."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._jobs: List[RegisteredJob] = []
        self._stop_event = threading.Event()

    def register_all(self) -> List[RegisteredJob]:
        """Walk YAMLs + register one job per (company, task).

        After each job is registered the library sets `job.next_run`
        to ``now + interval``. We then subtract a deterministic
        per-(company, task) offset within the interval so multiple
        companies don't all fire on the exact same minute — avoids
        burst-rate-limit hits against shared upstreams like GitHub."""
        from briar.iac.runbook import load_runbook_file
        from briar.iac.runbook.executor import RunbookSchedules

        for path in sorted(self._dir.glob("*.yaml")):
            runbook = load_runbook_file(path)
            for company_name, company in runbook.companies.items():
                for entry in RunbookSchedules.for_company(company):
                    job = EveryParser.parse(entry.every)
                    job.do(self._make_callable(path, company_name, entry.task))
                    self._apply_stagger(job, company_name, entry.task)
                    self._jobs.append(RegisteredJob(company_name, entry.task, entry.every, job))
        return list(self._jobs)

    @staticmethod
    def _apply_stagger(job: schedule.Job, company: str, task: str) -> None:
        """Rebase the job's next_run so two (company, task) pairs with the
        same cadence don't fire in lockstep. The offset is bounded by the
        cadence (a 1-hour job staggers 0–59 minutes; a 4-hour job staggers
        0–239 minutes) and deterministic per (company, task) — restarts
        produce the same offset. We do NOT shift jobs whose cadence is
        already pinned via `.at("HH:MM")` because the user explicitly
        wanted that wall-clock anchor."""
        if job.next_run is None or job.at_time is not None:
            return
        unit = job.unit or ""
        if unit not in {"minutes", "hours", "days", "weeks"}:
            return
        interval = timedelta(**{unit: job.interval})
        cadence_seconds = int(interval.total_seconds())
        if cadence_seconds < 60:
            return
        seed = f"{company}:{task}".encode("utf-8")
        digest = int(hashlib.sha1(seed).hexdigest(), 16)
        offset_seconds = digest % cadence_seconds
        delta = timedelta(seconds=offset_seconds) - interval
        job.next_run = job.next_run + delta
        log.debug(
            "stagger: company=%s task=%s cadence_s=%d offset_s=%d next_run=%s",
            company,
            task,
            cadence_seconds,
            offset_seconds,
            job.next_run,
        )

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
