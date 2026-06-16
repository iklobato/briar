"""Dashboard tests — monitoring collectors + rendered output (no real HTTP)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from briar.dashboard.collectors import (
    CollectorRegistry,
    ConnectivityCollector,
    CycleOutcomeCollector,
    DashboardPaths,
    DashboardSelf,
    GitDeployCollector,
    SchedulesCollector,
)
from briar.dashboard.server import DashboardServer

_RUNBOOK_YAML = """\
version: 1
companies:
  acme:
    extract:
      - name: pr-archaeology
        args:
          pr_repo: [acme/widgets]
    schedules:
      - task: prfix
        every: hour
        extract: []
"""

_LOG_LINES = (
    "[2026-05-20T01:00:00Z] extract examples/acme.yaml\n"
    "acme   wrote 1234 bytes via store=file   ./knowledge/acme.md\n"
    "[2026-05-20T01:00:01Z] extract examples/other.yaml\n"
    "FAILED other.yaml: some error\n"
    "[2026-05-20T01:00:01Z] cycle done\n"
)


class SchedulesCollectorTests(unittest.TestCase):
    def test_reads_yaml_into_per_task_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "acme.yaml").write_text(_RUNBOOK_YAML)
            result = SchedulesCollector(examples_dir=Path(td)).collect()
        self.assertEqual(result["count"], 2)
        by_task = {r["task"]: r for r in result["rows"]}
        self.assertEqual(by_task["extractors"]["every"], "day at 03:17")
        self.assertEqual(by_task["prfix"]["every"], "hour")
        self.assertEqual(by_task["extractors"]["extractors"], ["pr-archaeology"])
        # next_fire must render even when the extract list is empty.
        self.assertTrue(by_task["prfix"]["next_fire"])


class CycleOutcomeTests(unittest.TestCase):
    def test_parses_ok_and_failed(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(_LOG_LINES)
            f.close()
            result = CycleOutcomeCollector(log_path=Path(f.name)).collect()
        self.assertEqual(len(result["cycles"]), 1)
        statuses = {r["company"]: r["status"] for r in result["cycles"][0]["rows"]}
        self.assertEqual(statuses["acme"], "ok")
        self.assertEqual(statuses["other"], "failed")


class GitDeployCollectorTests(unittest.TestCase):
    def test_missing_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = GitDeployCollector(repo_path=Path(td)).collect()
        self.assertFalse(result["present"])

    def test_real_repo_reads_short_sha(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            import subprocess

            subprocess.run(["git", "init", "-q", "-b", "main", td], check=True)
            (Path(td) / "a.txt").write_text("x")
            subprocess.run(["git", "-C", td, "-c", "user.email=t@t", "-c", "user.name=t", "add", "."], check=True)
            subprocess.run(["git", "-C", td, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], check=True)
            result = GitDeployCollector(repo_path=Path(td)).collect()
        self.assertTrue(result["present"])
        self.assertEqual(len(result["short_sha"]), 7)
        self.assertEqual(result["subject"], "init")
        self.assertEqual(result["branch"], "main")


class ConnectivityCollectorTests(unittest.TestCase):
    def test_unreachable_target(self) -> None:
        # Reserved unreachable port — connect should fail fast.
        result = ConnectivityCollector(targets=(("127.0.0.1", 1),), timeout=0.5).collect()
        self.assertEqual(len(result["rows"]), 1)
        self.assertFalse(result["rows"][0]["reachable"])


class FullRenderTests(unittest.TestCase):
    def test_render_has_monitoring_sections_and_no_chartjs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ex = base / "examples"
            ex.mkdir()
            (ex / "acme.yaml").write_text(_RUNBOOK_YAML)
            log = base / "log"
            log.write_text(_LOG_LINES)

            paths = DashboardPaths(examples_dir=ex, log_path=log, disk_path=base, repo_path=base)
            dash = DashboardSelf(started_at=0.0, request_count_fn=lambda: 7, last_render_ms_fn=lambda: 42.0)
            server = DashboardServer()
            server.set_collectors(CollectorRegistry.from_paths(paths, dash))
            html = server.render_index()

        for needle in (
            "briar monitor",
            "at a glance",
            "<h2>connectivity",
            "<h2>schedulers",
            "github api quota",
            "recent cycles",
            "recent activity",
            "acme",
        ):
            self.assertIn(needle, html, f"missing: {needle!r}")
        # The redesign is self-contained — no CDN, no client-side charting.
        for forbidden in ("chart.umd.min.js", "cdn.jsdelivr", "<canvas", "new Chart"):
            self.assertNotIn(forbidden, html, f"should be gone: {forbidden!r}")


if __name__ == "__main__":
    unittest.main()
