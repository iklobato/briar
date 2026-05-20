"""Dashboard tests — collectors + rendered output (no real HTTP)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from briar.dashboard.collectors import (
    CollectorRegistry,
    CompaniesCollector,
    CronCollector,
    KnowledgeCollector,
    ScheduleLogCollector,
)
from briar.dashboard.server import DashboardServer


_RUNBOOK_YAML = """\
version: 1
companies:
  acme:
    knowledge:
      name: ./knowledge/acme.md
    extract:
      - name: pr-archaeology
        args:
          pr_repo: [iklobato/lightapi]
      - name: active-work
        args:
          active_repo: [iklobato/lightapi]
"""

_CRON_FILE = """\
SHELL=/bin/sh
PATH=/x/bin:/usr/bin
# nightly extraction
17 3 * * * root cd /opt/foo && briar runbook sweep examples/ >> /tmp/x.log 2>&1
"""

_LOG_LINES = (
    "[2026-05-20T01:00:00Z] extract examples/acme.yaml\n"
    "[2026-05-20T01:00:01Z] cycle done\n"
)


class CompaniesCollectorTests(unittest.TestCase):
    def test_reads_yaml_and_extractor_names(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            yaml_path = Path(td) / "acme.yaml"
            yaml_path.write_text(_RUNBOOK_YAML)
            result = CompaniesCollector(examples_dir=Path(td)).collect()
        self.assertEqual(result["count"], 1)
        row = result["rows"][0]
        self.assertEqual(row["company"], "acme")
        self.assertIn("pr-archaeology", row["extractors"])
        self.assertIn("active-work", row["extractors"])
        self.assertEqual(row["knowledge_file"], "./knowledge/acme.md")

    def test_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = CompaniesCollector(examples_dir=Path(td)).collect()
        self.assertEqual(result["count"], 0)


class CronCollectorTests(unittest.TestCase):
    def test_parses_entry(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".cron", delete=False) as f:
            f.write(_CRON_FILE)
            f.close()
            result = CronCollector(cron_path=Path(f.name)).collect()
        self.assertTrue(result["present"])
        self.assertEqual(len(result["entries"]), 1)
        entry = result["entries"][0]
        self.assertEqual(entry["schedule"], "17 3 * * *")
        self.assertEqual(entry["user"], "root")
        self.assertIn("briar runbook sweep", entry["command"])

    def test_missing(self) -> None:
        result = CronCollector(cron_path=Path("/nonexistent/cron")).collect()
        self.assertFalse(result["present"])


class KnowledgeCollectorTests(unittest.TestCase):
    def test_lists_md_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "acme.md").write_text("# Acme\n\nhello\n")
            (root / "subdir").mkdir()
            (root / "subdir" / "x.md").write_text("# X\n")
            rows = KnowledgeCollector(root=root).collect()["rows"]
        self.assertEqual({r["path"] for r in rows}, {"acme.md", "subdir/x.md"})

    def test_missing_root(self) -> None:
        result = KnowledgeCollector(root=Path("/nonexistent")).collect()
        self.assertTrue(result["missing"])


class ScheduleLogCollectorTests(unittest.TestCase):
    def test_tails_and_finds_cycle_marker(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(_LOG_LINES)
            f.close()
            result = ScheduleLogCollector(log_path=Path(f.name)).collect()
        self.assertTrue(result["present"])
        self.assertEqual(len(result["lines"]), 2)
        self.assertIn("cycle done", result["last_cycle"])


class RenderTests(unittest.TestCase):
    def test_full_render_contains_every_section(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ex = Path(td) / "examples"
            ex.mkdir()
            (ex / "acme.yaml").write_text(_RUNBOOK_YAML)
            kn = Path(td) / "knowledge"
            kn.mkdir()
            (kn / "acme.md").write_text("# Acme knowledge\n")
            cron = Path(td) / "cron"
            cron.write_text(_CRON_FILE)
            log = Path(td) / "log"
            log.write_text(_LOG_LINES)

            collectors = CollectorRegistry.for_paths(
                examples_dir=ex,
                knowledge_dir=kn,
                cron_path=cron,
                log_path=log,
                disk_path=Path(td),
            )
            html = DashboardServer(collectors=collectors).render_index()

        for needle in (
            "<title>briar scheduler",
            "<h2>system",
            "<h2>schedulers",
            "<h2>companies",
            "<h2>knowledge files",
            "<h2>extractors",
            "<h2>source templates",
            "<h2>trigger templates",
            "<h2>storage backends",
            "<h2>recent activity",
            "acme",
            "pr-archaeology",
            "github_webhook",
        ):
            self.assertIn(needle, html, f"missing: {needle!r}")


if __name__ == "__main__":
    unittest.main()
