"""ScheduleCalendarCollector — verifies bucketing, past-log parsing,
and future projection across the ±24h window."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from briar.dashboard.collectors import ScheduleCalendarCollector


_RUNBOOK = """\
version: 1

companies:
  acme:
    knowledge:
      store: file
      name: knowledge:acme

    schedules:
      - task: prfix
        every: "hour"
        extract:
          - name: active-work
            args: {active_repo: [foo/bar]}
      - task: implementation
        every: "4 hours"
        extract:
          - name: codebase-conventions
            args: {conventions_repo: [foo/bar]}
      - task: extractors
        every: "day at 03:17"
        extract:
          - name: pr-archaeology
            args: {pr_repo: [foo/bar]}
"""


_LOG_LINES = """\
2026-05-20T18:10:00Z [INFO   ] briar.iac.runbook.scheduler: fire task=prfix company=acme yaml=acme.yaml
2026-05-20T18:10:02Z [INFO   ] briar.iac.runbook.scheduler: result task=prfix company=acme status=wrote 3727 bytes via store=postgres output=knowledge:acme.prfix
2026-05-20T19:10:00Z [INFO   ] briar.iac.runbook.scheduler: fire task=prfix company=acme yaml=acme.yaml
2026-05-20T19:10:01Z [INFO   ] briar.iac.runbook.scheduler: result task=prfix company=acme status=wrote 100 bytes via store=postgres output=knowledge:acme.prfix
2026-05-20T20:10:00Z [INFO   ] briar.iac.runbook.scheduler: fire task=implementation company=acme yaml=acme.yaml
2026-05-20T20:10:05Z [INFO   ] briar.iac.runbook.scheduler: result task=implementation company=acme status=FAILED some-error
"""


class ScheduleCalendarTests(unittest.TestCase):
    def _build(self, log_path: Path) -> ScheduleCalendarCollector:
        td = Path(tempfile.mkdtemp())
        (td / "acme.yaml").write_text(_RUNBOOK)
        return ScheduleCalendarCollector(examples_dir=td, log_path=log_path)

    def test_window_is_48_hours_plus_one_marker_bucket(self) -> None:
        c = self._build(Path("/nonexistent"))
        r = c.collect()
        # 25 future-side rows (now-hour + 24 ahead) + 24 past rows = 49 buckets.
        self.assertEqual(len(r["buckets"]), 49)

    def test_one_bucket_marked_as_now(self) -> None:
        c = self._build(Path("/nonexistent"))
        r = c.collect()
        now_buckets = [b for b in r["buckets"] if b["is_now"]]
        self.assertEqual(len(now_buckets), 1)

    def test_buckets_partition_around_now(self) -> None:
        c = self._build(Path("/nonexistent"))
        r = c.collect()
        before = [b for b in r["buckets"] if b["is_past"]]
        now_b = [b for b in r["buckets"] if b["is_now"]]
        after = [b for b in r["buckets"] if not b["is_past"] and not b["is_now"]]
        self.assertEqual(len(before), 24)
        self.assertEqual(len(now_b), 1)
        self.assertEqual(len(after), 24)

    def test_future_projection_walks_forward(self) -> None:
        """The hourly schedule should produce ~24 future fires
        spread across distinct hours, not all clustered at one minute."""
        c = self._build(Path("/nonexistent"))
        r = c.collect()
        prfix_hours = sorted({f["when"] for b in r["buckets"] for f in b["fires"] if f["task"] == "prfix" and f["kind"] == "future"})
        # `hour` cadence yields a fresh "HH:MM" every hour. We don't
        # require all 24 (window-edge clipping is OK) but we do require
        # the spread to be well into the double digits.
        self.assertGreaterEqual(len(prfix_hours), 20)

    def test_past_fires_pair_with_results(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write(_LOG_LINES)
            log_path = Path(fh.name)
        # Pin "now" by mocking datetime.now in the collector module. The
        # log lines are dated 2026-05-20 18:10 to 20:10; pretend now is
        # 2026-05-20 21:00 so all three fires sit inside the past-24h
        # window.
        import briar.dashboard.collectors as mod

        original = mod.datetime

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                return datetime(2026, 5, 20, 21, 0, 0, tzinfo=timezone.utc).astimezone(tz) if tz else datetime(2026, 5, 20, 21, 0, 0)

        mod.datetime = _Frozen  # type: ignore[misc, assignment]
        try:
            c = self._build(log_path)
            r = c.collect()
        finally:
            mod.datetime = original  # type: ignore[misc, assignment]

        past_by_status: dict = {}
        for b in r["buckets"]:
            for f in b["fires"]:
                if f["kind"] == "past":
                    past_by_status.setdefault(f["status"], []).append(f)
        # Two `ok` (prfix at 18:10 + 19:10) and one `failed` (implementation 20:10).
        self.assertEqual(len(past_by_status.get("ok", [])), 2)
        self.assertEqual(len(past_by_status.get("failed", [])), 1)
        # `bytes` field captured for the ok ones.
        ok_bytes = sorted(f["bytes"] for f in past_by_status["ok"])
        self.assertEqual(ok_bytes, [100, 3727])


if __name__ == "__main__":
    unittest.main()
