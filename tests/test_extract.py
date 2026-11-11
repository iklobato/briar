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
from briar.extract.composer import render_markdown
from briar.extract.language_detectors import LANGUAGE_DETECTORS

from tests.unit.fake_logins import FakeLogins


class RegistryTests(unittest.TestCase):
    def test_all_extractors_registered(self) -> None:
        for name in (
            "pr-archaeology",
            "aws-infra",
            "active-work",
            "github-deployments",
            "codebase-conventions",
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
                    title="Two",
                    body="",
                    subsections=[ExtractedSection(title="Sub", body="x")],
                ),
            ],
        )
        self.assertIn("# Briar knowledge base — acme", md)
        self.assertIn("## One", md)
        self.assertIn("### Sub", md)


class ExtractPrArchaeologyTests(unittest.TestCase):
    def test_extracts_summary(self) -> None:
        from briar.extract._provider import PullRequest, RepositoryProvider

        class FakeProvider(RepositoryProvider):
            kind = "fake"

            def __init__(self, *, company: str = "") -> None:
                self._company = company

            def is_available(self) -> bool:
                return True

            def resolve_token(self) -> str:
                return "PLACEHOLDER-not-a-secret"

            def clone_url(self, owner, repo):
                return f"https://fake/{owner}/{repo}.git"

            def authed_clone_url(self, owner, repo, token):
                return f"https://x-user:{token}@fake/{owner}/{repo}.git"

            def pr_creation_recipe(self, *, owner, repo, branch):
                return "  6. fake.\n  7. done.\n"

            def list_pulls(self, repo, *, state, max_count):
                return [
                    PullRequest(
                        number=1,
                        title="t1",
                        author="alice",
                        is_draft=False,
                        head_ref="f",
                        base_ref="main",
                        review_comment_count=0,
                        created_at="2026-05-09T22:00:00Z",
                        merged_at="2026-05-10T00:00:00Z",
                        requested_reviewers=["reviewer1"],
                    ),
                    PullRequest(
                        number=2,
                        title="t2",
                        author="alice",
                        is_draft=False,
                        head_ref="g",
                        base_ref="main",
                        review_comment_count=0,
                        created_at="2026-05-09T21:00:00Z",
                        merged_at="2026-05-10T00:00:00Z",
                    ),
                ]

            def read_file(self, repo, path):
                return ""

        ext = EXTRACTORS["pr-archaeology"]
        provider = FakeProvider()
        with mock.patch("briar.extract.base.make_provider", return_value=provider, create=True):
            with mock.patch.object(ext, "_provider", return_value=provider):
                args = argparse.Namespace(pr_repo=["o/r"], pr_max=10, provider="fake", company="")
                # is_available also calls _provider — covered by the same patch.
                self.assertTrue(ext.is_available(args))
                section = ext.extract(args)
        self.assertEqual(section.title, "PR archaeology — 1 repo(s)")
        repo_section = section.subsections[0]
        self.assertEqual(repo_section.data["merged_pr_count"], 2)
        self.assertEqual(repo_section.data["top_authors"], [("alice", 2)])


class ExtractAwsInfraTests(unittest.TestCase):
    def test_unreachable_aws_renders_friendly_section(self) -> None:
        ext = EXTRACTORS["aws-infra"]
        with mock.patch("boto3.Session") as session_cls:
            instance = session_cls.return_value
            instance.client.return_value.get_caller_identity.side_effect = RuntimeError("boom")
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
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "db-1",
                    "Engine": "postgres",
                    "EngineVersion": "15",
                    "DBInstanceClass": "db.t3.medium",
                    "AllocatedStorage": 32,
                    "MultiAZ": True,
                }
            ],
        }
        section = gatherer.gather(session)
        self.assertIn("db-1", section.body)
        self.assertIn("Multi-AZ", section.body)


class LanguageDetectorTests(unittest.TestCase):
    """Phase 12 flattened the per-detector subclass files into a single
    `language_detectors/__init__.py` module with a frozen dataclass +
    one `_detect_*` function per language. Tests now resolve detectors
    by name through the public registry."""

    @staticmethod
    def _by_name(name: str):
        from briar.extract.language_detectors import LANGUAGE_DETECTORS

        for d in LANGUAGE_DETECTORS:
            if d.name == name:
                return d
        raise AssertionError(f"no detector named {name!r} in registry")

    def test_python_detector(self) -> None:
        det = self._by_name("python")
        text = "[tool.pytest.ini_options]\n[tool.ruff]\n[tool.alembic]\n"
        result = det.detect("o/r", lambda r, p: text if p == det.manifest else "")
        self.assertEqual(result["language"], "python")
        self.assertEqual(result["test_runner"], "pytest")
        self.assertEqual(result["linter"], "ruff")
        self.assertEqual(result["migrations"], "alembic")

    def test_python_detector_missing_manifest_returns_empty(self) -> None:
        det = self._by_name("python")
        self.assertEqual(det.detect("o/r", lambda r, p: ""), {})

    def test_node_detector_typescript_promotion(self) -> None:
        det = self._by_name("node")
        text = '{"dependencies": {"typescript": "*", "vitest": "*"}}'
        result = det.detect("o/r", lambda r, p: text)
        self.assertEqual(result["language"], "typescript")
        self.assertEqual(result["test_runner"], "vitest")

    def test_go_detector(self) -> None:
        det = self._by_name("go")
        result = det.detect("o/r", lambda r, p: "module example.com/x\n")
        self.assertEqual(result["language"], "go")
        self.assertEqual(result["test_runner"], "go test")


class RepositoryProviderTests(unittest.TestCase):
    """Provider registry + per-provider plumbing. No network."""

    def test_registry_lists_github_and_bitbucket(self) -> None:
        from briar.extract._providers import RepositoryProviderRegistry

        self.assertIn("github", RepositoryProviderRegistry.kinds())
        self.assertIn("bitbucket", RepositoryProviderRegistry.kinds())

    def test_unknown_provider_raises(self) -> None:
        from briar.errors import CliError
        from briar.extract._providers import make_provider

        with self.assertRaises(CliError):
            make_provider("perforce", FakeLogins().logins())

    def test_github_provider_uses_the_company_token(self) -> None:
        from briar.extract._providers import make_provider

        provider = make_provider("github", FakeLogins(environ={"BRIAR_ACME_GITHUB_TOKEN": "PLACEHOLDER-not-a-secret"}).logins())
        self.assertTrue(provider.is_available())

    def test_github_provider_unavailable_without_token(self) -> None:
        from briar.extract._providers import make_provider

        self.assertFalse(make_provider("github", FakeLogins().logins()).is_available())

    def test_bitbucket_provider_reads_per_company_creds(self) -> None:
        from briar.extract._providers import make_provider

        environ = {"BRIAR_ACME_BITBUCKET_EMAIL": "bot@example.test", "BRIAR_ACME_BITBUCKET_TOKEN": "PLACEHOLDER-not-a-secret"}
        provider = make_provider("bitbucket", FakeLogins({"bitbucket": {"workspace": "acme"}}, environ).logins())
        self.assertTrue(provider.is_available())

    def test_bitbucket_provider_unavailable_without_company_creds(self) -> None:
        from briar.extract._providers import make_provider

        self.assertFalse(make_provider("bitbucket", FakeLogins({"bitbucket": {"workspace": "acme"}}).logins()).is_available())

    def test_pull_request_dataclass_is_provider_neutral(self) -> None:
        """The PR shape extractors consume must NOT carry provider-specific
        field names. A field rename here is a deliberate breaking change."""
        from briar.extract._provider import PullRequest

        pr = PullRequest(
            number=42,
            title="t",
            author="alice",
            is_draft=False,
            head_ref="f",
            base_ref="main",
            review_comment_count=3,
            created_at="2026-05-21T00:00:00Z",
        )
        # The fields below are the contract every provider adapter
        # must populate. Adding one here = updating every provider.
        for field_name in (
            "number",
            "title",
            "author",
            "is_draft",
            "head_ref",
            "base_ref",
            "review_comment_count",
            "created_at",
            "merged_at",
            "requested_reviewers",
            "body",
        ):
            self.assertTrue(hasattr(pr, field_name), f"PullRequest must expose {field_name!r}")


class ExtractCodebaseConventionsTests(unittest.TestCase):
    def test_orchestrator_uses_python_detector(self) -> None:
        from briar.extract._provider import RepositoryProvider

        py_text = "[tool.pytest.ini_options]\n[tool.ruff]\n[tool.alembic]\n"

        class FakeProvider(RepositoryProvider):
            kind = "fake"

            def __init__(self, *, company: str = "") -> None:
                self._company = company

            def is_available(self) -> bool:
                return True

            def resolve_token(self) -> str:
                return "PLACEHOLDER-not-a-secret"

            def clone_url(self, owner, repo):
                return f"https://fake/{owner}/{repo}.git"

            def authed_clone_url(self, owner, repo, token):
                return f"https://x-user:{token}@fake/{owner}/{repo}.git"

            def pr_creation_recipe(self, *, owner, repo, branch):
                return "  6. fake.\n  7. done.\n"

            def list_pulls(self, repo, *, state, max_count):
                return []

            def read_file(self, repo, path):
                return py_text if path == "pyproject.toml" else ""

        ext = EXTRACTORS["codebase-conventions"]
        provider = FakeProvider()
        with mock.patch.object(ext, "_provider", return_value=provider):
            args = argparse.Namespace(conventions_repo=["o/r"], provider="fake", company="")
            self.assertTrue(ext.is_available(args))
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
        with (
            mock.patch(
                "briar.extract._gh.GithubApi.get_paginated",
                return_value=[
                    {
                        "merged_at": "2026-05-10T00:00:00Z",
                        "created_at": "2026-05-09T22:00:00Z",
                        "user": {"login": "u"},
                        "requested_reviewers": [],
                    }
                ],
            ),
            mock.patch.dict("os.environ", {"BRIAR_ACME_GITHUB_TOKEN": "PLACEHOLDER-not-a-secret"}),
        ):
            rb = load_runbook_file(Path(f.name))
            with tempfile.TemporaryDirectory() as td:
                rb.companies["acme"].knowledge_file = f"{td}/acme.md"
                rows = extract_runbook(rb)
                self.assertEqual(len(rows), 1)
                self.assertTrue(Path(f"{td}/acme.md").exists())


class GithubProviderPrSurfaceTests(unittest.TestCase):
    """Translation from GitHub REST payloads into provider-neutral
    dataclasses. No network — patch GithubApi.get_paginated / get_json."""

    def test_to_pull_populates_body(self) -> None:
        from briar.extract._providers.github import GithubProvider

        raw = {
            "number": 7,
            "title": "Add cache",
            "user": {"login": "alice"},
            "head": {"ref": "fix/cache", "sha": "deadbeef"},
            "base": {"ref": "main"},
            "draft": False,
            "review_comments": 2,
            "created_at": "2026-05-01T00:00:00Z",
            "merged_at": None,
            "requested_reviewers": [{"login": "bob"}],
            "body": "## Summary\nfixes the cache key collision",
        }
        pr = GithubProvider._to_pull(raw)
        self.assertEqual(pr.number, 7)
        self.assertIn("cache key collision", pr.body)

    def test_to_pull_caps_body_at_5000_chars(self) -> None:
        from briar.extract._providers.github import GithubProvider

        pr = GithubProvider._to_pull({"number": 1, "body": "x" * 50_000})
        self.assertEqual(len(pr.body), 5000)

    def test_list_pr_comments_merges_reviews_with_state_prefix(self) -> None:
        """Review submissions (Approve / Request changes) must appear in
        the merged comment list with a state prefix so the agent can see
        the high-level verdicts, not just the line-level threads."""
        from briar.extract._providers.github import GithubProvider

        def fake_paginated(path, max_pages=2):
            if path.endswith("/comments") and "pulls" in path:
                return []
            if path.endswith("/comments") and "issues" in path:
                return []
            if path.endswith("/reviews"):
                return [
                    {"id": 1, "user": {"login": "bob"}, "state": "CHANGES_REQUESTED", "body": "please add a test", "submitted_at": "2026-05-02T00:00:00Z"},
                    {"id": 2, "user": {"login": "carol"}, "state": "APPROVED", "body": "", "submitted_at": "2026-05-02T01:00:00Z"},
                    {"id": 3, "user": {"login": "dan"}, "state": "COMMENTED", "body": "", "submitted_at": "2026-05-02T02:00:00Z"},
                ]
            return []

        with mock.patch("briar.extract._gh.GithubApi.get_paginated", side_effect=fake_paginated):
            comments = GithubProvider().list_pr_comments("acme/app", 7)
        bodies = [c.body for c in comments]
        self.assertTrue(any("[CHANGES_REQUESTED]" in b and "add a test" in b for b in bodies))
        # A pure-state APPROVED with no body is still useful — the agent
        # should know someone approved.
        self.assertTrue(any("[APPROVED]" in b for b in bodies))
        # A COMMENTED review with no body is noise — skip it.
        self.assertFalse(any("[COMMENTED]" in b for b in bodies))

    def test_list_ci_failures_uses_output_title_as_step(self) -> None:
        """The previous implementation looped over `annotations_url`
        (a string) and `string and [] or []` always evaluated to []
        leaving step_name empty. Pin the fixed behavior."""
        from briar.extract._providers.github import GithubProvider

        pr_json = {"head": {"sha": "deadbeef"}}
        check_runs_json = {
            "check_runs": [
                {
                    "id": 9001,
                    "name": "pytest",
                    "conclusion": "failure",
                    "output": {"title": "3 tests failed", "summary": "..."},
                    "html_url": "https://github.com/acme/app/runs/9001",
                }
            ]
        }

        def fake_get_json(path):
            if path.endswith("/pulls/7"):
                return pr_json
            if "/check-runs" in path:
                return check_runs_json
            return {}

        with (
            mock.patch("briar.extract._gh.GithubApi.get_json", side_effect=fake_get_json),
            mock.patch.object(GithubProvider, "_tail_check_run_log", return_value="log tail"),
        ):
            failures = GithubProvider().list_ci_failures("acme/app", 7)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].step, "3 tests failed")
        self.assertEqual(failures[0].log_tail, "log tail")


class BitbucketProviderHelpersTests(unittest.TestCase):
    """Pure-function helpers — no library / no network."""

    def test_pipeline_failed_recognises_failed(self) -> None:
        from briar.extract._providers.bitbucket import BitbucketProvider

        self.assertTrue(BitbucketProvider._pipeline_failed({"state": {"result": {"name": "FAILED"}}}))

    def test_pipeline_failed_recognises_error(self) -> None:
        from briar.extract._providers.bitbucket import BitbucketProvider

        self.assertTrue(BitbucketProvider._pipeline_failed({"state": {"result": {"name": "ERROR"}}}))

    def test_pipeline_failed_rejects_successful_and_in_progress(self) -> None:
        from briar.extract._providers.bitbucket import BitbucketProvider

        self.assertFalse(BitbucketProvider._pipeline_failed({"state": {"result": {"name": "SUCCESSFUL"}}}))
        self.assertFalse(BitbucketProvider._pipeline_failed({"state": {"name": "IN_PROGRESS"}}))
        self.assertFalse(BitbucketProvider._pipeline_failed({}))

    def test_tail_pipeline_step_log_returns_last_lines(self) -> None:
        from briar.extract._providers.bitbucket import BitbucketProvider

        class FakeRepo:
            def get(self, path, not_json_response=False):
                return "\n".join(f"line{i}" for i in range(1, 201))

        tail = BitbucketProvider._tail_pipeline_step_log(FakeRepo(), "pipe-uuid", "step-uuid")
        lines = tail.splitlines()
        self.assertEqual(len(lines), 80)
        self.assertEqual(lines[0], "line121")
        self.assertEqual(lines[-1], "line200")

    def test_tail_pipeline_step_log_handles_bytes(self) -> None:
        from briar.extract._providers.bitbucket import BitbucketProvider

        class FakeRepo:
            def get(self, path, not_json_response=False):
                return b"one\ntwo\nthree"

        self.assertEqual(BitbucketProvider._tail_pipeline_step_log(FakeRepo(), "p", "s"), "one\ntwo\nthree")

    def test_tail_pipeline_step_log_empty_when_uuids_missing(self) -> None:
        from briar.extract._providers.bitbucket import BitbucketProvider

        self.assertEqual(BitbucketProvider._tail_pipeline_step_log(object(), "", "s"), "")
        self.assertEqual(BitbucketProvider._tail_pipeline_step_log(object(), "p", ""), "")


class BitbucketToPullTests(unittest.TestCase):
    """`_to_pull` translates the library's PullRequest object (or its
    backing data dict) into the provider-neutral dataclass. The library
    doesn't surface `draft` / `description` as direct attributes on
    every version — `_to_pull` falls through to the raw `data` dict."""

    class _FakePr:
        """Minimal duck-type of atlassian-python-api's PullRequest."""

        id = 7
        title = "Add cache"
        author = None
        source_branch = "fix/cache"
        destination_branch = "main"
        comment_count = 0
        created_on = "2026-05-01T00:00:00Z"
        updated_on = "2026-05-02T00:00:00Z"
        reviewers: List = []

        def __init__(self, **overrides):
            self.data = overrides.pop("data", {})
            for k, v in overrides.items():
                setattr(self, k, v)

    def test_draft_falls_through_to_data_dict(self) -> None:
        from briar.extract._providers.bitbucket import BitbucketProvider

        pr = BitbucketProvider._to_pull(self._FakePr(data={"draft": True}), state="open")
        self.assertTrue(pr.is_draft)

    def test_draft_defaults_to_false_when_absent(self) -> None:
        from briar.extract._providers.bitbucket import BitbucketProvider

        pr = BitbucketProvider._to_pull(self._FakePr(data={}), state="open")
        self.assertFalse(pr.is_draft)

    def test_description_from_dict_raw_shape(self) -> None:
        """Bitbucket Cloud returns `description: {"raw": "...", "markup": "markdown"}`
        in the v2 response. `_to_pull` must dig into `.raw`."""
        from briar.extract._providers.bitbucket import BitbucketProvider

        pr = BitbucketProvider._to_pull(
            self._FakePr(data={"description": {"raw": "## Summary\nfixes cache"}}),
            state="open",
        )
        self.assertIn("Summary", pr.body)

    def test_description_from_string_shape(self) -> None:
        """Some library versions hand back description as a flat string."""
        from briar.extract._providers.bitbucket import BitbucketProvider

        pr = BitbucketProvider._to_pull(
            self._FakePr(data={"description": "plain markdown"}),
            state="open",
        )
        self.assertEqual(pr.body, "plain markdown")

    def test_description_capped_at_5000_chars(self) -> None:
        from briar.extract._providers.bitbucket import BitbucketProvider

        pr = BitbucketProvider._to_pull(
            self._FakePr(data={"description": "x" * 50_000}),
            state="open",
        )
        self.assertEqual(len(pr.body), 5000)


if __name__ == "__main__":
    unittest.main()
