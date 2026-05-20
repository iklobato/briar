"""Knowledge-extractor tests.

External clients (GitHub HTTP, boto3 sessions) are stubbed so the
tests run offline. We verify section structure, not exact markdown."""

from __future__ import annotations

import argparse
import unittest
from unittest import mock

from briar.extract import EXTRACTORS
from briar.extract.aws_services import AWS_SERVICE_GATHERERS
from briar.extract.base import ExtractedSection
from briar.extract.composer import render_json, render_markdown
from briar.extract.language_detectors import LANGUAGE_DETECTORS


class RegistryTests(unittest.TestCase):
    def test_all_extractors_registered(self) -> None:
        for name in (
            "pr-archaeology", "aws-infra", "active-work",
            "github-deployments", "codebase-conventions",
        ):
            self.assertIn(name, EXTRACTORS)

    def test_aws_sub_strategy_registered(self) -> None:
        for name in ("ecs", "rds", "lambda", "sqs", "logs"):
            self.assertIn(name, AWS_SERVICE_GATHERERS)

    def test_language_detectors_registered(self) -> None:
        names = {d.name for d in LANGUAGE_DETECTORS}
        self.assertEqual(names, {"python", "node", "go"})


class ComposerTests(unittest.TestCase):
    def test_markdown_header_and_sections(self) -> None:
        md = render_markdown(
            company="acme",
            sections=[
                ExtractedSection(title="One", body="hello"),
                ExtractedSection(
                    title="Two", body="",
                    subsections=[ExtractedSection(title="Sub", body="x")],
                ),
            ],
        )
        self.assertIn("# Briar knowledge base — acme", md)
        self.assertIn("## One", md)
        self.assertIn("### Sub", md)

    def test_json_carries_structure(self) -> None:
        import json
        out = render_json(
            company="acme",
            sections=[ExtractedSection(title="One", body="hi", data={"k": 1})],
        )
        d = json.loads(out)
        self.assertEqual(d["company"], "acme")
        self.assertEqual(d["sections"][0]["data"], {"k": 1})


class ExtractPrArchaeologyTests(unittest.TestCase):
    def test_extracts_summary(self) -> None:
        ext = EXTRACTORS["pr-archaeology"]
        fake_prs = [
            {
                "merged_at": "2026-05-10T00:00:00Z",
                "created_at": "2026-05-09T22:00:00Z",
                "user": {"login": "iklobato"},
                "requested_reviewers": [{"login": "reviewer1"}],
            },
            {
                "merged_at": "2026-05-10T00:00:00Z",
                "created_at": "2026-05-09T21:00:00Z",
                "user": {"login": "iklobato"},
                "requested_reviewers": [],
            },
        ]
        with mock.patch(
            "briar.extract.pr_archaeology.get_paginated",
            return_value=fake_prs,
        ), mock.patch(
            "briar.extract.pr_archaeology.auth_token",
            return_value="fake-token",
        ):
            args = argparse.Namespace(pr_repo=["o/r"], pr_max=10)
            section = ext.extract(args)
        self.assertIsNotNone(section)
        self.assertEqual(section.title, "PR archaeology — 1 repo(s)")
        repo_section = section.subsections[0]
        self.assertEqual(repo_section.data["merged_pr_count"], 2)
        self.assertEqual(repo_section.data["top_authors"], [("iklobato", 2)])


class ExtractAwsInfraTests(unittest.TestCase):
    def test_unreachable_aws_renders_friendly_section(self) -> None:
        ext = EXTRACTORS["aws-infra"]
        with mock.patch("boto3.Session") as session_cls:
            instance = session_cls.return_value
            instance.client.return_value.get_caller_identity.side_effect = (
                RuntimeError("boom")
            )
            args = argparse.Namespace(
                aws_extract_profile=None,
                aws_extract_region="us-east-1",
                aws_extract_service=[],
            )
            section = ext.extract(args)
        self.assertIsNotNone(section)
        self.assertIn("UNREACHABLE", section.title)


class AwsServiceGathererTests(unittest.TestCase):
    """Each gatherer's contract: take a boto3 Session, return an
    ExtractedSection (possibly the empty sentinel)."""

    def test_ecs_empty_section_when_no_clusters(self) -> None:
        gatherer = AWS_SERVICE_GATHERERS["ecs"]
        session = mock.MagicMock()
        session.client.return_value.list_clusters.return_value = {"clusterArns": []}
        section = gatherer.gather(session)
        self.assertEqual(section.title, "ECS")
        self.assertIn("no services", section.body)

    def test_rds_renders_instance(self) -> None:
        gatherer = AWS_SERVICE_GATHERERS["rds"]
        session = mock.MagicMock()
        session.client.return_value.describe_db_instances.return_value = {
            "DBInstances": [{
                "DBInstanceIdentifier": "db-1",
                "Engine": "postgres",
                "EngineVersion": "15",
                "DBInstanceClass": "db.t3.medium",
                "AllocatedStorage": 32,
                "MultiAZ": True,
            }],
        }
        section = gatherer.gather(session)
        self.assertIn("db-1", section.body)
        self.assertIn("Multi-AZ", section.body)


class LanguageDetectorTests(unittest.TestCase):
    def test_python_detector(self) -> None:
        from briar.extract.language_detectors.python import DetectPython
        det = DetectPython()
        text = "[tool.pytest.ini_options]\n[tool.ruff]\n[tool.alembic]\n"
        result = det.detect("o/r", lambda r, p: text if p == det.manifest else None)
        self.assertEqual(result["language"], "python")
        self.assertEqual(result["test_runner"], "pytest")
        self.assertEqual(result["linter"], "ruff")
        self.assertEqual(result["migrations"], "alembic")

    def test_python_detector_missing_manifest_returns_none(self) -> None:
        from briar.extract.language_detectors.python import DetectPython
        det = DetectPython()
        self.assertIsNone(det.detect("o/r", lambda r, p: None))

    def test_node_detector_typescript_promotion(self) -> None:
        from briar.extract.language_detectors.node import DetectNode
        det = DetectNode()
        text = '{"dependencies": {"typescript": "*", "vitest": "*"}}'
        result = det.detect("o/r", lambda r, p: text)
        self.assertEqual(result["language"], "typescript")
        self.assertEqual(result["test_runner"], "vitest")

    def test_go_detector(self) -> None:
        from briar.extract.language_detectors.go import DetectGo
        det = DetectGo()
        result = det.detect("o/r", lambda r, p: "module example.com/x\n")
        self.assertEqual(result["language"], "go")
        self.assertEqual(result["test_runner"], "go test")


class ExtractCodebaseConventionsTests(unittest.TestCase):
    def test_orchestrator_uses_python_detector(self) -> None:
        ext = EXTRACTORS["codebase-conventions"]
        py_text = "[tool.pytest.ini_options]\n[tool.ruff]\n[tool.alembic]\n"
        with mock.patch(
            "briar.extract.codebase_conventions._read_repo_file",
            side_effect=lambda r, p: py_text if p == "pyproject.toml" else None,
        ), mock.patch(
            "briar.extract.codebase_conventions.auth_token",
            return_value="t",
        ):
            args = argparse.Namespace(conventions_repo=["o/r"])
            section = ext.extract(args)
        sub = section.subsections[0]
        self.assertEqual(sub.data["language"], "python")
        self.assertEqual(sub.data["linter"], "ruff")


class RunbookExtractTests(unittest.TestCase):
    """End-to-end: runbook YAML with `extract:` → executor.extract_runbook
    writes the markdown to disk."""

    def test_extract_writes_knowledge_file(self) -> None:
        import tempfile
        from pathlib import Path

        from briar.iac.runbook import extract_runbook, load_runbook_file

        yaml = """
version: 1
companies:
  acme:
    profile: acme
    knowledge_file: ./knowledge/acme.md
    extract:
      - name: pr-archaeology
        args:
          pr_repo: [o/r]
    runbooks:
      - template: implementation
        prefix: x
        owner: o
        repo: r
        sources:
          - kind: github
        trigger:
          kind: schedule_cron
"""
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        f.write(yaml)
        f.close()
        with mock.patch(
            "briar.extract.pr_archaeology.get_paginated",
            return_value=[{
                "merged_at": "2026-05-10T00:00:00Z",
                "created_at": "2026-05-09T22:00:00Z",
                "user": {"login": "u"},
                "requested_reviewers": [],
            }],
        ), mock.patch(
            "briar.extract.pr_archaeology.auth_token",
            return_value="fake",
        ):
            rb = load_runbook_file(Path(f.name))
            with tempfile.TemporaryDirectory() as td:
                rb.companies["acme"].knowledge_file = f"{td}/acme.md"
                rows = extract_runbook(rb)
                self.assertEqual(len(rows), 1)
                self.assertTrue(Path(f"{td}/acme.md").exists())


if __name__ == "__main__":
    unittest.main()
