"""Smart-scheduler optimizations — verifies the three additive layers
(output-hash dedup, fingerprint backend, per-company stagger)."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from briar.storage import make_store


class FingerprintTests(unittest.TestCase):
    def test_file_store_fingerprint_matches_md5_of_content(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = make_store("file", file_root=Path(td))
            content = "# hello world\nsome content"
            store.put("knowledge:acme", content)
            expected = hashlib.md5(content.encode("utf-8")).hexdigest()
            self.assertEqual(store.fingerprint("knowledge:acme"), expected)

    def test_fingerprint_returns_empty_for_missing_blob(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = make_store("file", file_root=Path(td))
            self.assertEqual(store.fingerprint("knowledge:missing"), "")

    def test_fingerprint_updates_on_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = make_store("file", file_root=Path(td))
            store.put("knowledge:acme", "first")
            first = store.fingerprint("knowledge:acme")
            store.put("knowledge:acme", "second")
            second = store.fingerprint("knowledge:acme")
        self.assertNotEqual(first, second)


class StaggerTests(unittest.TestCase):
    def test_two_companies_with_same_cadence_get_distinct_next_run(self) -> None:
        import schedule

        from briar.iac.runbook.scheduler import EveryParser, RunbookScheduler

        scheduler_a = schedule.Scheduler()
        scheduler_b = schedule.Scheduler()
        job_a = EveryParser.parse("hour", scheduler=scheduler_a)
        job_b = EveryParser.parse("hour", scheduler=scheduler_b)
        job_a.do(lambda: None)
        job_b.do(lambda: None)

        RunbookScheduler._apply_stagger(job_a, "widgets", "prfix")
        RunbookScheduler._apply_stagger(job_b, "acme", "prfix")
        self.assertIsNotNone(job_a.next_run)
        self.assertIsNotNone(job_b.next_run)
        # Different (company, task) seeds must hash to different offsets
        # for the test to be meaningful. With 60min cadence the offset
        # space is 3600 distinct values, so collisions on two seeds are
        # negligibly rare.
        self.assertNotEqual(job_a.next_run, job_b.next_run)

    def test_stagger_is_deterministic_across_calls(self) -> None:
        import schedule

        from briar.iac.runbook.scheduler import EveryParser, RunbookScheduler

        next_runs = []
        for _ in range(2):
            local = schedule.Scheduler()
            job = EveryParser.parse("hour", scheduler=local)
            job.do(lambda: None)
            base = job.next_run
            RunbookScheduler._apply_stagger(job, "acme", "prfix")
            next_runs.append((base, job.next_run))
        # The offset applied (next_run - base) must be the same for the
        # same (company, task) input across calls — restarts should not
        # randomize fire times.
        delta_a = next_runs[0][1] - next_runs[0][0]
        delta_b = next_runs[1][1] - next_runs[1][0]
        # `now` advanced between the two calls so absolute times differ,
        # but the offset relative to the library's initial next_run should
        # be stable to the second.
        self.assertEqual(delta_a, delta_b)

    def test_stagger_skips_at_time_anchored_jobs(self) -> None:
        """`day at 03:17` schedules pin to a wall-clock anchor — we
        must not shift them."""
        import schedule

        from briar.iac.runbook.scheduler import EveryParser, RunbookScheduler

        local = schedule.Scheduler()
        job = EveryParser.parse("day at 03:17", scheduler=local)
        job.do(lambda: None)
        before = job.next_run
        RunbookScheduler._apply_stagger(job, "acme", "extractors")
        self.assertEqual(job.next_run, before)


if __name__ == "__main__":
    unittest.main()
