"""Scaffold template tests — the composer + per-registry behaviour."""

from __future__ import annotations

import argparse
import unittest

from briar.iac import TEMPLATES
from briar.iac.scaffold.archetypes import ARCHETYPES
from briar.iac.scaffold.shapes import WORKFLOW_SHAPES
from briar.iac.scaffold.sources import SOURCE_TEMPLATES
from briar.iac.scaffold.triggers import TRIGGER_TEMPLATES


def _ns(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    # Sensible defaults so individual tests only override what they care about.
    defaults = {
        "owner": "iklobato",
        "repo": "lightapi",
        "prefix": "test",
        "source": ["github"],
        "archetype": "engineer",
        "shape": "plan-approve-act",
        "trigger_kind": "github_webhook",
        "llm_provider_key": "anthropic",
        "model": "claude-sonnet-4-6",
        "auth_mode": "oauth",
        "github_secret_id": None,
        "jira_project": [],
        "jira_jql": None,
        "jira_secret_id": None,
        "aws_role_arn": None,
        "aws_external_id": None,
        "aws_region": "us-east-1",
        "aws_services": [],
        "webhook_events": [],
        "webhook_labels": ["briar"],
        "schedule": "0 * * * *",
    }
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(ns, k, v)
    return ns


class ImplementationTemplateTests(unittest.TestCase):
    def test_default_github_oauth(self) -> None:
        tmpl = TEMPLATES["implementation"]
        bundle = tmpl.build(_ns())
        for section in ("llm_models", "sources", "tools", "agents", "workflows", "triggers"):
            self.assertIn(section, bundle)
        # Agent references one source + the github tool family.
        agent = bundle["agents"][0]
        self.assertEqual(agent["llm_model_key"], "test-model")
        self.assertEqual(len(agent["source_keys"]), 1)
        self.assertGreater(len(agent["tool_keys"]), 0)

    def test_multi_source_tracker_plus_cloud(self) -> None:
        tmpl = TEMPLATES["implementation"]
        bundle = tmpl.build(_ns(source=["github", "jira", "aws"]))
        # One source row per kind.
        kinds = sorted(s["kind"] for s in bundle["sources"])
        self.assertEqual(kinds, ["aws", "github", "jira"])
        # Tools come from github (3) + jira (3); aws is read-only (0).
        impls = sorted(t["implementation_ref"] for t in bundle["tools"])
        self.assertEqual(len(impls), 6)
        self.assertTrue(any("github." in i for i in impls))
        self.assertTrue(any("jira." in i for i in impls))

    def test_archetype_filters_tools(self) -> None:
        # Triager keeps comment-style tools; drops commit / open_pr.
        tmpl = TEMPLATES["implementation"]
        bundle = tmpl.build(_ns(source=["github"], archetype="triager"))
        refs = [t["implementation_ref"] for t in bundle["tools"]]
        self.assertIn("github.comment_on_issue", refs)
        self.assertNotIn("github.commit_files", refs)
        self.assertNotIn("github.open_pr", refs)

    def test_shape_one_shot_drops_human_checkpoint(self) -> None:
        tmpl = TEMPLATES["implementation"]
        bundle = tmpl.build(_ns(shape="one-shot"))
        kinds = [n["kind"] for n in bundle["workflows"][0]["graph"]["nodes"]]
        self.assertNotIn("human_checkpoint", kinds)

    def test_cron_trigger(self) -> None:
        tmpl = TEMPLATES["implementation"]
        bundle = tmpl.build(_ns(trigger_kind="schedule_cron", schedule="*/15 * * * *"))
        self.assertEqual(bundle["triggers"][0]["kind"], "schedule")
        self.assertEqual(bundle["triggers"][0]["schedule_cron"], "*/15 * * * *")

    def test_manual_trigger_emits_no_trigger_row(self) -> None:
        tmpl = TEMPLATES["implementation"]
        bundle = tmpl.build(_ns(trigger_kind="manual"))
        self.assertNotIn("triggers", bundle)


class PrFixesTemplateTests(unittest.TestCase):
    def test_default_uses_pr_fixer_archetype_and_one_shot(self) -> None:
        tmpl = TEMPLATES["pr-fixes"]
        bundle = tmpl.build(_ns(archetype="pr-fixer", shape="one-shot"))
        agent = bundle["agents"][0]
        self.assertTrue(agent["name"].endswith("-pr-fixer"))
        # commit + comment tools present, transition-style ones absent
        refs = [t["implementation_ref"] for t in bundle["tools"]]
        self.assertIn("github.commit_files", refs)
        self.assertNotIn("github.add_labels", refs)


class RegistryShapesTests(unittest.TestCase):
    """Every registry is non-empty and self-consistent."""

    def test_sources_registered(self) -> None:
        for kind in ("github", "jira", "aws"):
            self.assertIn(kind, SOURCE_TEMPLATES)

    def test_archetypes_registered(self) -> None:
        for name in ("engineer", "pr-fixer", "triager"):
            self.assertIn(name, ARCHETYPES)

    def test_shapes_registered(self) -> None:
        for name in ("plan-approve-act", "one-shot", "triage"):
            self.assertIn(name, WORKFLOW_SHAPES)

    def test_triggers_registered(self) -> None:
        for name in ("github_webhook", "schedule_cron", "manual"):
            self.assertIn(name, TRIGGER_TEMPLATES)


if __name__ == "__main__":
    unittest.main()
