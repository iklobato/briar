"""Dashboard tests — collectors + rendered output (no real HTTP)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from briar.dashboard.collectors import (
    ArchetypesCollector,
    AwsServicesCollector,
    CollectorRegistry,
    CommandsCollector,
    CompaniesCollector,
    ConnectivityCollector,
    CronCollector,
    CycleOutcomeCollector,
    GitDeployCollector,
    KnowledgeAggregatesCollector,
    KnowledgeCollector,
    LanguageDetectorsCollector,
    ScheduleLogCollector,
    SecretsCollector,
    WorkflowShapesCollector,
    _cron_field,
    _human_bytes,
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
17 3 * * * root cd /opt/foo && briar runbook sweep examples/ >> /tmp/x.log 2>&1
"""

_LOG_LINES = (
    "[2026-05-20T01:00:00Z] extract examples/acme.yaml\n"
    "acme   wrote 1234 bytes via store=file   ./knowledge/acme.md\n"
    "[2026-05-20T01:00:01Z] extract examples/other.yaml\n"
    "FAILED other.yaml: some error\n"
    "[2026-05-20T01:00:01Z] cycle done\n"
)

_KNOWLEDGE_SAMPLE = """\
# Briar knowledge — acme

## PR archaeology — 1 repo(s)
- merged PR sample: **42**

## Active work — 1 repo(s)
### acme/repo — 7 open PR(s)

## AWS infrastructure
### RDS (3 instance(s))
### SQS (2 queue(s))
### CloudWatch Logs (top 10 by size, of 15)
"""


class CompaniesCollectorTests(unittest.TestCase):
    def test_reads_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "acme.yaml").write_text(_RUNBOOK_YAML)
            result = CompaniesCollector(examples_dir=Path(td)).collect()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["rows"][0]["company"], "acme")


class KnowledgeAggregatesTests(unittest.TestCase):
    def test_mines_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "acme.md").write_text(_KNOWLEDGE_SAMPLE)
            result = KnowledgeAggregatesCollector(knowledge_root=Path(td)).collect()
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["merged_prs"], 42)
        self.assertEqual(result["open_prs"], 7)
        self.assertEqual(result["rds_instances"], 3)
        self.assertEqual(result["sqs_queues"], 2)
        self.assertEqual(result["log_groups"], 15)


class CronCollectorTests(unittest.TestCase):
    def test_parses_entry_and_next_fire(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".cron", delete=False) as f:
            f.write(_CRON_FILE)
            f.close()
            result = CronCollector(cron_path=Path(f.name)).collect()
        self.assertTrue(result["present"])
        entry = result["entries"][0]
        self.assertEqual(entry["schedule"], "17 3 * * *")
        self.assertEqual(entry["user"], "root")
        # next_fire should be a non-empty ISO-ish string
        self.assertTrue(entry["next_fire"])


class CronFieldTests(unittest.TestCase):
    def test_star(self) -> None:
        self.assertEqual(_cron_field("*", 0, 4), {0, 1, 2, 3, 4})

    def test_int(self) -> None:
        self.assertEqual(_cron_field("5", 0, 59), {5})

    def test_range(self) -> None:
        self.assertEqual(_cron_field("3-5", 0, 59), {3, 4, 5})

    def test_step(self) -> None:
        self.assertEqual(_cron_field("*/2", 0, 6), {0, 2, 4, 6})

    def test_list(self) -> None:
        self.assertEqual(_cron_field("1,5,7", 0, 59), {1, 5, 7})


class CycleOutcomeTests(unittest.TestCase):
    def test_parses_ok_and_failed(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write(_LOG_LINES)
            f.close()
            result = CycleOutcomeCollector(log_path=Path(f.name)).collect()
        self.assertEqual(len(result["cycles"]), 1)
        rows = result["cycles"][0]["rows"]
        self.assertEqual({r["company"] for r in rows}, {"acme", "other"})
        statuses = {r["company"]: r["status"] for r in rows}
        self.assertEqual(statuses["acme"], "ok")
        self.assertEqual(statuses["other"], "failed")


class SecretsCollectorTests(unittest.TestCase):
    def test_returns_names_and_lengths_only(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("FOO=hello\nBAR=12345\n# comment\nEMPTY=\n")
            f.close()
            result = SecretsCollector(secrets_path=Path(f.name)).collect()
        names = {r["name"]: (r["length"], r["set"]) for r in result["rows"]}
        self.assertEqual(names["FOO"], (5, True))
        self.assertEqual(names["BAR"], (5, True))
        self.assertEqual(names["EMPTY"], (0, False))
        # No value strings anywhere in output.
        for row in result["rows"]:
            self.assertNotIn("hello", str(row))
            self.assertNotIn("12345", str(row))


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
            subprocess.run(
                ["git", "-C", td, "-c", "user.email=t@t", "-c", "user.name=t",
                 "add", "."], check=True,
            )
            subprocess.run(
                ["git", "-C", td, "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", "init"],
                check=True,
            )
            result = GitDeployCollector(repo_path=Path(td)).collect()
        self.assertTrue(result["present"])
        self.assertEqual(len(result["short_sha"]), 7)
        self.assertEqual(result["subject"], "init")
        self.assertEqual(result["branch"], "main")


class ConnectivityCollectorTests(unittest.TestCase):
    def test_unreachable_target(self) -> None:
        # Reserved unreachable port — connect should fail fast.
        result = ConnectivityCollector(
            targets=(("127.0.0.1", 1),),
            timeout=0.5,
        ).collect()
        self.assertEqual(len(result["rows"]), 1)
        self.assertFalse(result["rows"][0]["reachable"])


class HumanBytesTests(unittest.TestCase):
    def test_units(self) -> None:
        self.assertEqual(_human_bytes(0), "0.0 B")
        self.assertEqual(_human_bytes(2048), "2.0 KB")
        self.assertEqual(_human_bytes(5 * 1024 * 1024), "5.0 MB")


class RegistryCollectorsTests(unittest.TestCase):
    """The five registry-backed collectors all return non-empty rows."""

    def test_aws_services(self) -> None:
        self.assertGreater(len(AwsServicesCollector().collect()["rows"]), 0)

    def test_language_detectors(self) -> None:
        rows = LanguageDetectorsCollector().collect()["rows"]
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(r["manifest"] for r in rows))

    def test_workflow_shapes(self) -> None:
        self.assertGreater(len(WorkflowShapesCollector().collect()["rows"]), 0)

    def test_archetypes(self) -> None:
        rows = ArchetypesCollector().collect()["rows"]
        self.assertGreater(len(rows), 0)
        self.assertTrue(all("role" in r for r in rows))

    def test_commands(self) -> None:
        names = {r["name"] for r in CommandsCollector().collect()["rows"]}
        self.assertIn("extract", names)
        self.assertIn("runbook", names)
        self.assertIn("dashboard", names)


class FullRenderTests(unittest.TestCase):
    def test_render_includes_every_section_and_chart_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            ex = base / "examples"; ex.mkdir()
            (ex / "acme.yaml").write_text(_RUNBOOK_YAML)
            kn = base / "knowledge"; kn.mkdir()
            (kn / "acme.md").write_text(_KNOWLEDGE_SAMPLE)
            cron = base / "cron"; cron.write_text(_CRON_FILE)
            log = base / "log"; log.write_text(_LOG_LINES)
            secrets = base / "secrets.env"; secrets.write_text("X=y\n")

            collectors = CollectorRegistry.for_paths(
                examples_dir=ex,
                knowledge_dir=kn,
                cron_path=cron,
                log_path=log,
                disk_path=base,
                repo_path=base,
                secrets_path=secrets,
                du_paths=[base],
                process_started_at=0.0,
                request_count_fn=lambda: 7,
                last_render_ms_fn=lambda: 42.0,
            )
            html = DashboardServer(collectors=collectors).render_index()

        for needle in (
            "<title>briar scheduler",
            "at a glance",
            "<canvas id=\"knowledgeChart\"",
            "<canvas id=\"cycleChart\"",
            "<h2>connectivity",
            "<h2>schedulers",
            "<h2>aggregated extraction",
            "<h2>companies",
            "<h2>knowledge files",
            "<h2>recent cycles",
            "<h2>secrets presence",
            "<h2>disk by directory",
            "<h2>extractors",
            "<h2>aws gatherers",
            "<h2>language detectors",
            "<h2>sources",
            "<h2>triggers",
            "<h2>workflow shapes",
            "<h2>archetypes",
            "<h2>commands",
            "<h2>recent activity",
            "acme",
            "pr-archaeology",
            "github_webhook",
            "chart.umd.min.js",
            "Chart.defaults",
        ):
            self.assertIn(needle, html, f"missing: {needle!r}")


if __name__ == "__main__":
    unittest.main()
