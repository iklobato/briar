"""EveryParser + RunbookScheduler tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import schedule

from briar.errors import ConfigError
from briar.iac.runbook.scheduler import EveryParser, RunbookScheduler


_YAML = """\
version: 1
companies:
  acme:
    knowledge:
      name: ./knowledge/acme.md
    schedules:
      - task: extractors
        every: "day at 03:17"
        extract:
          - name: pr-archaeology
            args: {pr_repo: [acme/widgets]}
      - task: prfix
        every: "hour"
        extract:
          - name: active-work
            args: {active_repo: [acme/widgets]}
"""


class EveryParserTests(unittest.TestCase):
    def _local(self) -> schedule.Scheduler:
        return schedule.Scheduler()

    def test_day_at_hhmm(self) -> None:
        job = EveryParser.parse("day at 03:17", scheduler=self._local())
        self.assertEqual(job.interval, 1)
        self.assertEqual(job.unit, "days")
        self.assertEqual(job.at_time.hour, 3)
        self.assertEqual(job.at_time.minute, 17)

    def test_four_hours(self) -> None:
        job = EveryParser.parse("4 hours", scheduler=self._local())
        self.assertEqual(job.interval, 4)
        self.assertEqual(job.unit, "hours")

    def test_hour(self) -> None:
        job = EveryParser.parse("hour", scheduler=self._local())
        self.assertEqual(job.interval, 1)
        self.assertEqual(job.unit, "hours")

    def test_hour_at_minute(self) -> None:
        job = EveryParser.parse("hour at :15", scheduler=self._local())
        self.assertEqual(job.unit, "hours")
        self.assertEqual(job.at_time.minute, 15)

    def test_weekday(self) -> None:
        job = EveryParser.parse("monday at 09:00", scheduler=self._local())
        self.assertEqual(job.unit, "weeks")
        self.assertIn("monday", str(job.start_day).lower())
        self.assertEqual(job.at_time.hour, 9)

    def test_invalid(self) -> None:
        with self.assertRaises(ConfigError):
            EveryParser.parse("eleven nanoseconds")

    def test_weekday_with_count_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            EveryParser.parse("3 monday")


class RunbookSchedulerTests(unittest.TestCase):
    def test_register_all_creates_one_job_per_task(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "acme.yaml").write_text(_YAML)
            scheduler = RunbookScheduler(Path(td))
            registered = scheduler.register_all()
        self.assertEqual(len(registered), 2)
        tasks = {r.task for r in registered}
        self.assertEqual(tasks, {"extractors", "prfix"})


if __name__ == "__main__":
    unittest.main()
