"""Runbook YAML schema + executor tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

from briar.errors import ConfigError
from briar.iac.runbook import RunbookFile, load_runbook_file
from briar.iac.runbook.executor import _build_namespace, apply_runbook


_MINIMAL_YAML = """
version: 1
companies:
  acme:
    profile: acme-test
    runbooks:
      - template: implementation
        prefix: impl
        owner: iklobato
        repo: lightapi
        sources:
          - kind: github
        trigger:
          kind: schedule_cron
          schedule: "0 * * * *"
"""


def _write_yaml(text: str) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


class LoadTests(unittest.TestCase):
    def test_minimal(self) -> None:
        rb = load_runbook_file(_write_yaml(_MINIMAL_YAML))
        self.assertIn("acme", rb.companies)
        acme = rb.companies["acme"]
        self.assertEqual(acme.profile, "acme-test")
        self.assertEqual(len(acme.runbooks), 1)
        runbook = acme.runbooks[0]
        self.assertEqual(runbook.template, "implementation")
        self.assertEqual(runbook.trigger.kind, "schedule_cron")

    def test_unknown_field_rejected(self) -> None:
        path = _write_yaml(_MINIMAL_YAML.replace(
            "profile: acme-test", "profile: acme-test\n    typo_field: x"
        ))
        with self.assertRaises(ConfigError):
            load_runbook_file(path)

    def test_unknown_source_kind_rejected(self) -> None:
        path = _write_yaml(_MINIMAL_YAML.replace(
            "kind: github", "kind: linear"
        ))
        with self.assertRaises(ConfigError):
            load_runbook_file(path)

    def test_aws_source_with_role(self) -> None:
        yaml = """
version: 1
companies:
  widgets:
    profile: pt-test
    runbooks:
      - template: implementation
        prefix: pt-impl
        owner: o
        repo: r
        sources:
          - kind: aws
            role_arn: arn:aws:iam::123:role/briar
            external_id: pt-id
            services: [ec2, s3]
        trigger:
          kind: schedule_cron
"""
        rb = load_runbook_file(_write_yaml(yaml))
        src = rb.companies["widgets"].runbooks[0].sources[0]
        self.assertEqual(src.kind, "aws")
        self.assertEqual(src.role_arn, "arn:aws:iam::123:role/briar")
        self.assertEqual(src.services, ["ec2", "s3"])


class NamespaceBuildingTests(unittest.TestCase):
    def test_defaults_inherit_into_namespace(self) -> None:
        rb = load_runbook_file(_write_yaml(_MINIMAL_YAML))
        runbook = rb.companies["acme"].runbooks[0]
        ns = _build_namespace(runbook, rb.companies["acme"].defaults)
        self.assertEqual(ns.prefix, "impl")
        self.assertEqual(ns.source, ["github"])
        self.assertEqual(ns.trigger_kind, "schedule_cron")
        self.assertEqual(ns.schedule, "0 * * * *")
        # pr-fixes is NOT applied here; default archetype is engineer.
        self.assertEqual(ns.archetype, "engineer")

    def test_pr_fixes_swaps_defaults(self) -> None:
        yaml = _MINIMAL_YAML.replace("template: implementation",
                                     "template: pr-fixes")
        rb = load_runbook_file(_write_yaml(yaml))
        runbook = rb.companies["acme"].runbooks[0]
        ns = _build_namespace(runbook, None)
        # pr-fixes scaffold has its own implicit defaults.
        self.assertEqual(ns.archetype, "pr-fixer")
        self.assertEqual(ns.shape, "one-shot")

    def test_per_company_defaults_override_scaffold(self) -> None:
        yaml = """
version: 1
companies:
  acme:
    profile: acme
    defaults:
      auth_mode: pat
      github_secret_id: acme-secret-uuid
    runbooks:
      - template: implementation
        prefix: acme-impl
        owner: o
        repo: r
        sources:
          - kind: github
        trigger:
          kind: schedule_cron
"""
        rb = load_runbook_file(_write_yaml(yaml))
        runbook = rb.companies["acme"].runbooks[0]
        ns = _build_namespace(runbook, rb.companies["acme"].defaults)
        self.assertEqual(ns.auth_mode, "pat")
        self.assertEqual(ns.github_secret_id, "acme-secret-uuid")

    def test_jira_aws_kind_specific_config_flattens(self) -> None:
        yaml = """
version: 1
companies:
  acme:
    profile: acme
    runbooks:
      - template: implementation
        prefix: u
        owner: o
        repo: r
        sources:
          - kind: jira
            project: [ACME, PLATFORM]
            jql: "status != Done"
          - kind: aws
            role_arn: arn:aws:iam::1:role/r
            services: [ec2]
        trigger:
          kind: schedule_cron
"""
        rb = load_runbook_file(_write_yaml(yaml))
        runbook = rb.companies["acme"].runbooks[0]
        ns = _build_namespace(runbook, None)
        self.assertEqual(ns.source, ["jira", "aws"])
        self.assertEqual(ns.jira_project, ["ACME", "PLATFORM"])
        self.assertEqual(ns.jira_jql, "status != Done")
        self.assertEqual(ns.aws_role_arn, "arn:aws:iam::1:role/r")
        self.assertEqual(ns.aws_services, ["ec2"])


class _StubClient:
    def __init__(self, prepopulate=None):
        self.calls: List[tuple] = []
        self.existing: Dict[str, List[Dict[str, Any]]] = prepopulate or {}
        self.create_counter = 0

    def list_all(self, base_path: str, query=None):
        return self.existing.get(base_path, [])

    def request(self, method, path, body=None, query=None):
        self.calls.append((method, path, body))
        if method == "POST":
            self.create_counter += 1
            return {"id": f"uuid-{self.create_counter}", **(body or {})}
        if method == "PATCH":
            return {"id": path.rstrip("/").split("/")[-1], **(body or {})}
        return None


class ExecutorTests(unittest.TestCase):
    def test_plan_runs_for_every_company(self) -> None:
        yaml = """
version: 1
companies:
  acme:
    profile: acme
    runbooks:
      - template: implementation
        prefix: u
        owner: o
        repo: r
        sources:
          - kind: github
        trigger:
          kind: schedule_cron
  widgets:
    profile: pt
    runbooks:
      - template: implementation
        prefix: p
        owner: o
        repo: r
        sources:
          - kind: github
        trigger:
          kind: schedule_cron
"""
        rb = load_runbook_file(_write_yaml(yaml))
        stub = _StubClient({
            "/api/v1/llm/providers/": [{"id": "prov-1", "name": "anthropic"}]
        })
        with mock.patch(
            "briar.iac.runbook.executor._client_for_company",
            lambda company: stub,
        ):
            rows = apply_runbook(rb, dry_run=True)
        companies = sorted({r[0] for r in rows})
        self.assertEqual(companies, ["widgets", "acme"])


if __name__ == "__main__":
    unittest.main()
