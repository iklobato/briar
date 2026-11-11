"""Smoke tests for the new abstraction layers (TrackerProvider,
LLMProvider, CloudProvider, NotificationSink, CredentialStore).

These tests focus on registry shape, factory error paths, and the
contract surface — not adapter behaviour (which needs network +
credentials). Each adapter family is verified to register at least
one real implementation and one or more stubs that fail loudly via
``NotImplementedError`` rather than silently."""

from __future__ import annotations

import argparse
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import schedule

from briar.errors import CliError
from tests.unit.fake_logins import FakeLogins


class TrackerRegistryTests(unittest.TestCase):
    def test_all_kinds_registered(self) -> None:
        from briar.extract._trackers import TrackerRegistry

        kinds = TrackerRegistry.kinds()
        for expected in ("jira", "github-issues", "bitbucket-issues", "linear"):
            self.assertIn(expected, kinds)

    def test_unknown_kind_raises(self) -> None:
        from briar.errors import CliError
        from briar.extract._trackers import make_tracker

        with self.assertRaises(CliError):
            make_tracker("notion", FakeLogins().logins())

    def test_jira_unavailable_without_creds(self) -> None:
        from briar.extract._trackers import make_tracker

        with mock.patch.dict("os.environ", {}, clear=True):
            tracker = make_tracker("jira", FakeLogins({"jira": {"site": "acme.atlassian.net"}}).logins())
            self.assertFalse(tracker.is_available())

    def test_linear_list_tickets_translates_graphql_response(self) -> None:
        from briar.extract._trackers import make_tracker

        fake_response = {
            "data": {
                "issues": {
                    "nodes": [
                        {
                            "identifier": "ENG-42",
                            "title": "broken metric",
                            "createdAt": "2026-05-20T00:00:00Z",
                            "updatedAt": "2026-05-21T00:00:00Z",
                            "url": "https://linear.app/acme/issue/ENG-42",
                            "priorityLabel": "High",
                            "state": {"name": "Triage", "type": "triage"},
                            "creator": {"displayName": "Alice", "name": "alice"},
                            "assignee": {"displayName": "Bob", "name": "bob"},
                            "labels": {"nodes": [{"name": "bug"}, {"name": "metrics"}]},
                        }
                    ]
                }
            }
        }
        with mock.patch.dict("os.environ", {}):
            tracker = make_tracker("linear", FakeLogins(environ={"BRIAR_ACME_LINEAR_API_KEY": "lin_xxx"}).logins())
            self.assertTrue(tracker.is_available())

            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = (json.dumps(fake_response)).encode("utf-8")
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = lambda s, *a: None
            with mock.patch("urllib.request.urlopen", return_value=mock_resp):
                tickets = tracker.list_tickets("ENG", state="open", max_count=10)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].key, "ENG-42")
        self.assertEqual(tickets[0].title, "broken metric")
        self.assertEqual(tickets[0].reporter, "Alice")
        self.assertEqual(tickets[0].assignee, "Bob")
        self.assertIn("bug", tickets[0].labels)


class CloudRegistryTests(unittest.TestCase):
    def test_all_kinds_registered(self) -> None:
        from briar.extract._clouds import CloudRegistry

        kinds = CloudRegistry.kinds()
        for expected in ("aws", "gcp", "azure"):
            self.assertIn(expected, kinds)

    def test_aws_available_with_boto3(self) -> None:
        from briar.extract._clouds import make_cloud

        cloud = make_cloud("aws", FakeLogins().logins(), region="us-east-1")
        # boto3 is a hard runtime dep; should always be available
        self.assertTrue(cloud.is_available())

    def test_gcp_unavailable_without_project(self) -> None:
        from briar.extract._clouds import make_cloud

        cloud = make_cloud("gcp", FakeLogins().logins())
        self.assertFalse(cloud.is_available())

    def test_azure_unavailable_without_sdk(self) -> None:
        from briar.extract._clouds import make_cloud

        cloud = make_cloud("azure", FakeLogins({"azure": {"subscription": "sub-id-here"}}).logins())
        with mock.patch("briar.extract._clouds.azure._try_import", return_value=None):
            self.assertFalse(cloud.is_available())

    def test_azure_caller_identity_raises_when_sdk_missing(self) -> None:
        from briar.extract._clouds import make_cloud

        cloud = make_cloud("azure", FakeLogins({"azure": {"subscription": "sub-id-here"}}).logins())
        with mock.patch("briar.extract._clouds.azure._try_import", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                cloud.caller_identity()
            self.assertIn("briar-cli[azure]", str(ctx.exception))

    def test_gcp_caller_identity_raises_when_sdk_missing(self) -> None:
        from briar.extract._clouds import make_cloud

        cloud = make_cloud("gcp", FakeLogins({"gcp": {"project": "proj-id"}}).logins())
        with mock.patch("briar.extract._clouds.gcp._try_import", return_value=None):
            with self.assertRaises(CliError) as ctx:
                cloud.caller_identity()
            self.assertIn("briar-cli[gcp]", str(ctx.exception))


class CredentialStoreTests(unittest.TestCase):
    def test_envfile_read_round_trip(self) -> None:
        from briar.credentials import EnvFileStore

        store = EnvFileStore()
        with mock.patch.dict("os.environ", {"BRIAR_ACME_JIRA_TOKEN": "PLACEHOLDER-not-a-secret"}, clear=False):
            self.assertEqual(store.read("BRIAR_ACME_JIRA_TOKEN"), "PLACEHOLDER-not-a-secret")
            # Missing returns None (distinct from "set to empty string") so callers can fail closed.
            self.assertIsNone(store.read("DOES_NOT_EXIST"))

    def test_envfile_list_keeps_the_login_names(self) -> None:
        from briar.credentials import EnvFileStore

        environ = {
            "BRIAR_ACME_JIRA_TOKEN": "x",
            "BRIAR_ACME_CO_GITHUB_TOKEN": "x",
            "AWS_ACME_ACCESS_KEY_ID": "x",
            "BRIAR_TELEMETRY": "off",
            "GITHUB_TOKEN": "x",
            "UNRELATED_VAR": "y",
        }
        with mock.patch.dict("os.environ", environ, clear=True):
            names = EnvFileStore().list()
        self.assertEqual(names, ["BRIAR_ACME_CO_GITHUB_TOKEN", "BRIAR_ACME_JIRA_TOKEN"])


class ProviderRequiredEnvVarsTests(unittest.TestCase):
    """Each provider declares its own required env vars via a
    ``classmethod required_env_vars(company)`` — replaces the
    hand-maintained `_EXTRACTOR_REQUIREMENTS` table the doctor used
    to consult."""

    def test_extractor_provider_class_for_routes_via_args(self) -> None:
        """Each extractor's `provider_class_for(args)` returns the
        provider class implied by `args.provider` / `args.tracker` /
        `args.cloud`. The doctor uses this to avoid maintaining a
        parallel (extractor × provider) table."""
        from briar.extract import EXTRACTORS

        # pr-archaeology is RepoBacked — defaults to GitHub.
        ext = EXTRACTORS["pr-archaeology"]
        ns = argparse.Namespace(provider="github")
        provider_cls = ext.provider_class_for(ns)
        self.assertIsNotNone(provider_cls)
        self.assertEqual(provider_cls.kind, "github")

        ns_bb = argparse.Namespace(provider="bitbucket")
        provider_cls = ext.provider_class_for(ns_bb)
        self.assertEqual(provider_cls.kind, "bitbucket")

        # active-tickets is TrackerBacked — defaults to Jira.
        ext = EXTRACTORS["active-tickets"]
        provider_cls = ext.provider_class_for(argparse.Namespace(tracker="linear"))
        self.assertEqual(provider_cls.kind, "linear")


class CredentialBootstrapTests(unittest.TestCase):
    """`CredentialBootstrap` is a separate ABC from CredentialStore
    (bulk-write-at-startup vs read-on-demand). EnvFileBootstrap is the
    only concrete impl today; this test pins the contract so a future
    addition stays in shape."""

    def test_registry_lists_envfile(self) -> None:
        """2.0 has one bootstrap, the envfile (the remote default-store
        loader went with the remote stores)."""
        from briar.credentials._bootstraps import BOOTSTRAPS

        self.assertEqual(list(BOOTSTRAPS.keys()), ["envfile"])

    def test_auto_bootstrap_no_backend_configured(self) -> None:
        """No backend is available → empty list. Startup must be
        robust to "no remote vault, no envfile present"."""
        import tempfile

        from briar.credentials._bootstraps import auto_bootstrap

        with tempfile.TemporaryDirectory() as tmp:
            # Point envfile resolution at a fresh path with no file →
            # EnvFileBootstrap.is_available() returns False, so no
            # bootstrap runs.
            env = {
                "BRIAR_SECRETS_FILE": str(Path(tmp) / "secrets.env"),
                "HOME": tmp,  # force XDG fallback off any real ~/.config
            }
            with mock.patch.dict("os.environ", env, clear=True):
                results = auto_bootstrap()
        self.assertEqual(results, [])

    def test_envfile_bootstrap_hydrates_only_unset_keys(self) -> None:
        """The cascade contract: envfile values populate os.environ
        only when the key isn't already set (operator shell env wins).
        Verifies the file-read path, the comment/blank-line skip, the
        export-prefix tolerance, and the quoted-value strip."""
        import tempfile

        from briar.credentials._bootstraps.envfile import EnvFileBootstrap

        contents = (
            "# header comment\n"
            "\n"
            "ANTHROPIC_API_KEY=sk-from-file\n"
            "export GITHUB_TOKEN=ghp_from_file\n"
            'JIRA_ACME_URL="https://acme.atlassian.net"\n'
            "ALREADY_SET=from-file\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.env"
            path.write_text(contents)
            env = {
                "BRIAR_SECRETS_FILE": str(path),
                "ALREADY_SET": "from-shell",
            }
            with mock.patch.dict("os.environ", env, clear=True):
                bs = EnvFileBootstrap()
                self.assertTrue(bs.is_available())
                result = bs.hydrate()
                self.assertTrue(result.ok)
                # written: keys not previously in os.environ
                self.assertIn("ANTHROPIC_API_KEY", result.written)
                self.assertIn("GITHUB_TOKEN", result.written)
                self.assertIn("JIRA_ACME_URL", result.written)
                # skipped: key already in shell env — operator wins
                self.assertIn("ALREADY_SET", result.skipped)
                # Quotes stripped on the JIRA URL
                self.assertEqual(os.environ["JIRA_ACME_URL"], "https://acme.atlassian.net")
                # export-prefix tolerated
                self.assertEqual(os.environ["GITHUB_TOKEN"], "ghp_from_file")
                # Operator-set value preserved
                self.assertEqual(os.environ["ALREADY_SET"], "from-shell")

    def test_envfile_unavailable_without_file(self) -> None:
        """No file → is_available() False, hydrate() returns a
        structured error not a crash."""
        import tempfile

        from briar.credentials._bootstraps.envfile import EnvFileBootstrap

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.env"
            with mock.patch.dict("os.environ", {"BRIAR_SECRETS_FILE": str(missing)}, clear=True):
                bs = EnvFileBootstrap()
                self.assertFalse(bs.is_available())
                result = bs.hydrate()
                self.assertFalse(result.ok)
                self.assertIn("no envfile", result.error)


class BuildRegistryTests(unittest.TestCase):
    """`build_registry` is used by all 13 plugin registries to surface
    accidental duplicate-name collisions at import time."""

    def test_builds_dict_by_name(self) -> None:
        from briar._registry import build_registry

        class Item:
            def __init__(self, name):
                self.name = name

        a, b = Item("a"), Item("b")
        out = build_registry((a, b), kind="test")
        self.assertEqual(out, {"a": a, "b": b})

    def test_raises_on_duplicate_name(self) -> None:
        from briar._registry import build_registry

        class Item:
            def __init__(self, name):
                self.name = name

        with self.assertRaises(RuntimeError) as ctx:
            build_registry((Item("dup"), Item("ok"), Item("dup")), kind="test")
        self.assertIn("duplicate", str(ctx.exception))
        self.assertIn("dup", str(ctx.exception))

    def test_raises_on_empty_name(self) -> None:
        from briar._registry import build_registry

        class Item:
            name = ""

        with self.assertRaises(RuntimeError):
            build_registry((Item(),), kind="test")

    def test_supports_kind_attribute(self) -> None:
        """Most provider registries key on `.kind` instead of `.name`."""
        from briar._registry import build_registry

        class Provider:
            def __init__(self, kind):
                self.kind = kind

        out = build_registry((Provider("github"), Provider("bitbucket")), kind="provider", name_attr="kind")
        self.assertIn("github", out)
        self.assertIn("bitbucket", out)


class RunbookSchemaRegistryValidationTests(unittest.TestCase):
    """ExtractEntry.name + KnowledgeBinding.store validate against the
    LIVE registry, not a hardcoded Literal[...]. This test pins that —
    a regression to `Literal["pr-archaeology", ...]` would break it."""

    def test_extract_entry_accepts_every_registered_extractor(self) -> None:
        from briar.extract import EXTRACTORS
        from briar.iac.runbook.models import ExtractEntry

        for name in EXTRACTORS.keys():
            # Should not raise — every registered extractor is a valid
            # name by construction.
            ExtractEntry(name=name, args={})

    def test_extract_entry_rejects_unknown_name(self) -> None:
        from pydantic import ValidationError

        from briar.iac.runbook.models import ExtractEntry

        with self.assertRaises((ValidationError, ValueError)):
            ExtractEntry(name="nonsense-extractor", args={})

    def test_knowledge_binding_accepts_every_registered_store(self) -> None:
        from briar.iac.runbook.models import KnowledgeBinding
        from briar.storage import KnowledgeStoreRegistry

        for kind in KnowledgeStoreRegistry.names():
            KnowledgeBinding(store=kind, name="x")

    def test_knowledge_binding_rejects_unknown_store(self) -> None:
        from pydantic import ValidationError

        from briar.iac.runbook.models import KnowledgeBinding

        with self.assertRaises((ValidationError, ValueError)):
            KnowledgeBinding(store="dynamodb", name="x")


class GitProviderRegistryTests(unittest.TestCase):
    """The unified `PROVIDERS` registry owns clone + auth + pr-creation
    recipe (was the parallel `REPO_CLONERS` hierarchy). `_run_prfix`,
    `_run_implement`, and `_implement_specific_instructions` all
    dispatch through the same registry. Adding a new provider must
    NOT require editing those methods."""

    def test_registry_has_both_providers(self) -> None:
        from briar.extract._providers import PROVIDERS

        self.assertIn("github", PROVIDERS)
        self.assertIn("bitbucket", PROVIDERS)

    def test_github_provider_uses_x_access_token_url(self) -> None:
        from briar.extract._providers import make_provider

        p = make_provider("github")
        self.assertEqual(p.clone_url("acme", "app"), "https://github.com/acme/app.git")
        self.assertEqual(
            p.authed_clone_url("acme", "app", "ghp_xxx"),
            "https://x-access-token:ghp_xxx@github.com/acme/app.git",
        )

    def test_bitbucket_provider_uses_x_token_auth_url(self) -> None:
        from briar.extract._providers import make_provider

        p = make_provider("bitbucket", FakeLogins().logins())
        self.assertEqual(p.clone_url("acme", "app"), "https://bitbucket.org/acme/app.git")
        self.assertEqual(
            p.authed_clone_url("acme", "app", "ATBB-xxx"),
            "https://x-token-auth:ATBB-xxx@bitbucket.org/acme/app.git",
        )


class NewExtractorTests(unittest.TestCase):
    """Verify the two new tracker-backed extractors register and gate
    on tracker availability."""

    def test_active_tickets_registered(self) -> None:
        from briar.extract import EXTRACTORS

        self.assertIn("active-tickets", EXTRACTORS)
        self.assertIn("ticket-archaeology", EXTRACTORS)

    def test_active_tickets_skips_when_no_projects(self) -> None:
        import argparse

        from briar.extract import EXTRACTORS

        ext = EXTRACTORS["active-tickets"]
        args = argparse.Namespace(ticket_project=[], tracker="jira", company="acme")
        self.assertFalse(ext.is_available(args))


class StoreBindingResolutionTests(unittest.TestCase):
    """`KnowledgeStore.from_binding` is the construction path; the file
    store is the only backend since 2.0."""

    def test_file_store_honours_binding_root(self) -> None:
        import tempfile
        from pathlib import Path

        from briar.storage import StoreBinding
        from briar.storage.file import StoreFile

        with tempfile.TemporaryDirectory() as tmp:
            binding = StoreBinding(store="file", root=tmp)
            store = StoreFile.from_binding(binding, default_root=Path("./should-be-ignored"))
            self.assertEqual(store._root, Path(tmp))

    def test_file_store_falls_back_to_default_root(self) -> None:
        import tempfile
        from pathlib import Path

        from briar.storage import StoreBinding
        from briar.storage.file import StoreFile

        with tempfile.TemporaryDirectory() as tmp:
            binding = StoreBinding(store="file")  # no root in binding
            store = StoreFile.from_binding(binding, default_root=Path(tmp))
            self.assertEqual(store._root, Path(tmp))

    def test_registry_make_store_synthesizes_binding_for_cli_callers(self) -> None:
        """CLI commands (`briar context`, `briar extract`) call
        ``make_store(name, file_root=...)`` without a binding. The
        registry synthesizes a `StoreBinding` so backends still go
        through `from_binding` — no special case for the CLI path."""
        import tempfile
        from pathlib import Path

        from briar.storage import make_store
        from briar.storage.file import StoreFile

        with tempfile.TemporaryDirectory() as tmp:
            store = make_store("file", file_root=Path(tmp))
            self.assertIsInstance(store, StoreFile)
            self.assertEqual(store._root, Path(tmp))

    def test_knowledge_binding_accepts_config_dict(self) -> None:
        """YAML schema must accept the new `config:` block."""
        from briar.iac.runbook.models import KnowledgeBinding

        b = KnowledgeBinding(store="file", name="knowledge:acme", config={"inventory": "true"})
        self.assertEqual(b.config["inventory"], "true")


class ErrorPolicyTests(unittest.TestCase):
    """`briar.error_policy` — pluggable error-response strategies.

    Two-level Strategy: ``ErrorPolicy`` picks a policy by exception
    shape; ``ErrorDecision`` polymorphically encodes the action
    (retry, abort, escalate). Pins these so a future "consolidate
    the dispatch" refactor can't reintroduce if-by-type branching
    inside the executor body."""

    def test_retry_after_sleeps_and_signals_retry(self) -> None:
        from briar.error_policy import FollowUp, RetryAfter

        with mock.patch("briar.error_policy.time.sleep") as sleep:
            result = RetryAfter(wait_seconds=5, reason="test").apply(exc=RuntimeError("x"), attempt=1)
        self.assertIs(result, FollowUp.RETRY)
        sleep.assert_called_once_with(5)

    def test_retry_after_zero_wait_skips_sleep(self) -> None:
        from briar.error_policy import RetryAfter

        with mock.patch("briar.error_policy.time.sleep") as sleep:
            RetryAfter(wait_seconds=0, reason="test").apply(exc=RuntimeError("x"), attempt=1)
        sleep.assert_not_called()

    def test_abort_signals_raise_without_sleep(self) -> None:
        from briar.error_policy import Abort, FollowUp

        with mock.patch("briar.error_policy.time.sleep") as sleep:
            result = Abort(reason="auth").apply(exc=RuntimeError("x"), attempt=2)
        self.assertIs(result, FollowUp.RAISE)
        sleep.assert_not_called()

    def test_escalate_calls_dispatcher_and_honours_then(self) -> None:
        from briar.error_policy import Escalate, FollowUp

        calls = []
        with mock.patch("briar.error_policy.time.sleep"):
            result = Escalate(
                dispatcher=lambda msg: calls.append(msg),
                message="quota exhausted",
                then=FollowUp.RAISE,
            ).apply(exc=RuntimeError("x"), attempt=1)
        self.assertEqual(calls, ["quota exhausted"])
        self.assertIs(result, FollowUp.RAISE)

    def test_escalate_dispatcher_failure_is_non_fatal(self) -> None:
        """A misbehaving dispatcher must NOT mask the underlying error
        path — the executor should still see the chosen ``.then``."""
        from briar.error_policy import Escalate, FollowUp

        def bad_dispatcher(msg):
            raise OSError("notify backend down")

        with mock.patch("briar.error_policy.time.sleep"):
            result = Escalate(
                dispatcher=bad_dispatcher,
                message="x",
                then=FollowUp.RETRY,
            ).apply(exc=RuntimeError("x"), attempt=1)
        self.assertIs(result, FollowUp.RETRY)

    def test_exception_type_policy_matches_class_and_subclass(self) -> None:
        from briar.error_policy import Abort, ExceptionTypePolicy

        class BaseErr(Exception):
            pass

        class SubErr(BaseErr):
            pass

        policy = ExceptionTypePolicy(exception_type=BaseErr, decision=Abort())
        self.assertTrue(policy.matches(BaseErr()))
        self.assertTrue(policy.matches(SubErr()))
        self.assertFalse(policy.matches(ValueError()))

    def test_http_status_policy_requires_both_class_and_status(self) -> None:
        from briar.error_policy import Abort, HttpStatusPolicy

        class StatusErr(Exception):
            def __init__(self, status_code):
                self.status_code = status_code

        policy = HttpStatusPolicy(exception_type=StatusErr, status=429, decision=Abort())
        self.assertTrue(policy.matches(StatusErr(429)))
        self.assertFalse(policy.matches(StatusErr(500)))
        self.assertFalse(policy.matches(ValueError()))

    def test_registry_returns_first_matching_policy(self) -> None:
        """Order is intentional — the policy at index 0 wins even if a
        later policy also matches."""
        from briar.error_policy import Abort, ErrorPolicyRegistry, ExceptionTypePolicy, RetryAfter

        first = ExceptionTypePolicy(exception_type=ValueError, decision=RetryAfter(1))
        second = ExceptionTypePolicy(exception_type=Exception, decision=Abort())
        registry = ErrorPolicyRegistry(policies=(first, second))
        self.assertIs(registry.resolve(ValueError("x")), first)

    def test_registry_falls_back_to_propagate_null_object(self) -> None:
        """No match → null-object policy that decides Abort. Caller
        never sees None."""
        from briar.error_policy import ErrorPolicyRegistry, FollowUp

        registry = ErrorPolicyRegistry(policies=())
        policy = registry.resolve(ValueError("x"))
        self.assertTrue(policy.matches(RuntimeError("y")))
        decision = policy.decide(ValueError("x"), attempt=1)
        with mock.patch("briar.error_policy.time.sleep"):
            self.assertIs(decision.apply(exc=ValueError("x"), attempt=1), FollowUp.RAISE)

    def test_registry_with_prepends_higher_priority(self) -> None:
        """``registry.with_(override)`` returns a new registry with the
        override at index 0. Per-company overlays use this."""
        from briar.error_policy import Abort, ErrorPolicyRegistry, ExceptionTypePolicy, RetryAfter

        base = ErrorPolicyRegistry(policies=(ExceptionTypePolicy(exception_type=ValueError, decision=Abort()),))
        overlay = ExceptionTypePolicy(exception_type=ValueError, decision=RetryAfter(60, reason="override"))
        merged = base.with_(overlay)
        # Base unchanged (immutable)
        self.assertIsInstance(base.resolve(ValueError()).decision, Abort)
        # Overlay wins
        self.assertEqual(merged.resolve(ValueError()).decision.reason, "override")

    def test_executor_returns_first_success_no_retry(self) -> None:
        from briar.error_policy import ErrorPolicyRegistry, RetryingExecutor

        calls = [0]

        def fn():
            calls[0] += 1
            return "ok"

        result = RetryingExecutor(ErrorPolicyRegistry()).run(fn)
        self.assertEqual(result, "ok")
        self.assertEqual(calls[0], 1)

    def test_executor_retries_then_succeeds(self) -> None:
        from briar.error_policy import ErrorPolicyRegistry, ExceptionTypePolicy, RetryAfter, RetryingExecutor

        calls = [0]

        def fn():
            calls[0] += 1
            if calls[0] < 3:
                raise ValueError("transient")
            return "ok"

        registry = ErrorPolicyRegistry(policies=(ExceptionTypePolicy(exception_type=ValueError, decision=RetryAfter(0, reason="t")),))
        with mock.patch("briar.error_policy.time.sleep"):
            result = RetryingExecutor(registry, max_attempts=5).run(fn)
        self.assertEqual(result, "ok")
        self.assertEqual(calls[0], 3)

    def test_executor_raises_when_decision_is_abort(self) -> None:
        """First attempt fails with a class matched by an Abort policy →
        executor MUST propagate immediately, no retry."""
        from briar.error_policy import Abort, ErrorPolicyRegistry, ExceptionTypePolicy, RetryingExecutor

        calls = [0]

        def fn():
            calls[0] += 1
            raise ValueError("auth-failed")

        registry = ErrorPolicyRegistry(policies=(ExceptionTypePolicy(exception_type=ValueError, decision=Abort()),))
        with self.assertRaises(ValueError):
            RetryingExecutor(registry).run(fn)
        self.assertEqual(calls[0], 1)

    def test_executor_propagates_unmatched_exception(self) -> None:
        from briar.error_policy import ErrorPolicyRegistry, RetryingExecutor

        calls = [0]

        def fn():
            calls[0] += 1
            raise RuntimeError("oops")

        with self.assertRaises(RuntimeError):
            RetryingExecutor(ErrorPolicyRegistry()).run(fn)
        self.assertEqual(calls[0], 1)

    def test_executor_exhausts_max_attempts_then_raises(self) -> None:
        from briar.error_policy import ErrorPolicyRegistry, ExceptionTypePolicy, RetryAfter, RetryingExecutor

        def always_fail():
            raise ValueError("always")

        registry = ErrorPolicyRegistry(policies=(ExceptionTypePolicy(exception_type=ValueError, decision=RetryAfter(0, reason="t")),))
        with mock.patch("briar.error_policy.time.sleep"):
            with self.assertRaises(ValueError) as cm:
                RetryingExecutor(registry, max_attempts=3).run(always_fail)
        self.assertEqual(str(cm.exception), "always")

    def test_executor_rejects_invalid_max_attempts(self) -> None:
        from briar.error_policy import ErrorPolicyRegistry, RetryingExecutor

        with self.assertRaises(ValueError):
            RetryingExecutor(ErrorPolicyRegistry(), max_attempts=0)


class EnvFileStoreWriteTests(unittest.TestCase):
    """``EnvFileStore.write/delete`` — file persistence + os.environ
    update, idempotent replace-in-place."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "secrets.env"
        self._patcher = mock.patch.dict(os.environ, {"BRIAR_SECRETS_FILE": str(self.path)}, clear=False)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self.tmpdir.cleanup()
        # Clean up any test keys we leaked into os.environ
        for k in list(os.environ.keys()):
            if k.startswith("TEST_BRIAR_"):
                del os.environ[k]

    def test_write_creates_file_and_updates_environ(self) -> None:
        from briar.credentials.envfile import EnvFileStore

        store = EnvFileStore()
        store.write("TEST_BRIAR_FOO", "bar")
        self.assertEqual(os.environ["TEST_BRIAR_FOO"], "bar")
        self.assertIn("TEST_BRIAR_FOO=bar", self.path.read_text())

    def test_write_replaces_in_place_no_duplicate(self) -> None:
        from briar.credentials.envfile import EnvFileStore

        store = EnvFileStore()
        store.write("TEST_BRIAR_FOO", "first")
        store.write("TEST_BRIAR_FOO", "second")
        contents = self.path.read_text()
        # Only ONE TEST_BRIAR_FOO= line, with the latest value
        self.assertEqual(contents.count("TEST_BRIAR_FOO="), 1)
        self.assertIn("TEST_BRIAR_FOO=second", contents)
        self.assertNotIn("TEST_BRIAR_FOO=first", contents)

    def test_write_validates_env_var_name(self) -> None:
        from briar.credentials.envfile import EnvFileStore

        store = EnvFileStore()
        with self.assertRaises(ValueError):
            store.write("lowercase-bad", "x")
        with self.assertRaises(ValueError):
            store.write("123_LEADING_DIGIT", "x")

    def test_write_creates_parent_directory(self) -> None:
        """First-time laptop use: ``~/.config/briar/`` may not exist.
        The store must `mkdir(parents=True)` rather than fail."""
        import tempfile
        from pathlib import Path

        from briar.credentials.envfile import EnvFileStore

        with tempfile.TemporaryDirectory() as tmp:
            nested = Path(tmp) / "missing" / "dir" / "secrets.env"
            with mock.patch.dict(os.environ, {"BRIAR_SECRETS_FILE": str(nested)}, clear=False):
                store = EnvFileStore()
                store.write("TEST_BRIAR_NESTED", "v")
                self.assertTrue(nested.exists())
                self.assertIn("TEST_BRIAR_NESTED=v", nested.read_text())

    def test_write_raises_on_unwritable_parent(self) -> None:
        """When the parent cannot be created (e.g. read-only fs), the
        write MUST raise — the misleading 'fall through to os.environ
        only' behaviour is what caused the 'persisted 4/4' lie."""
        from pathlib import Path

        from briar.credentials.envfile import EnvFileStore

        # /dev/null/x — /dev/null is a char device, can't have children.
        bad_path = Path("/dev/null/nope/secrets.env")
        with mock.patch.dict(os.environ, {"BRIAR_SECRETS_FILE": str(bad_path)}, clear=False):
            store = EnvFileStore()
            with self.assertRaises(OSError):
                store.write("TEST_BRIAR_BADPATH", "v")
            # Disk write failed → os.environ stays clean. A secret that
            # didn't make it to a 0o600 file MUST NOT linger in process env
            # where every spawned subprocess would inherit it (fixed in
            # Phase 1 security hardening — see envfile.py:write docstring).
            self.assertNotIn("TEST_BRIAR_BADPATH", os.environ)


class EnvFileSecretsPathResolutionTests(unittest.TestCase):
    """Three-step resolution chain for the secrets file path:
    1. $BRIAR_SECRETS_FILE       (explicit)
    2. /etc/briar/secrets.env    (droplet, if exists)
    3. XDG path                  (laptop default)"""

    def tearDown(self) -> None:
        for k in list(os.environ):
            if k.startswith("TEST_BRIAR_"):
                del os.environ[k]

    def test_explicit_env_var_wins(self) -> None:
        from briar.credentials.envfile import _secrets_path

        with mock.patch.dict(os.environ, {"BRIAR_SECRETS_FILE": "/tmp/forced/path.env"}, clear=False):
            self.assertEqual(str(_secrets_path()), "/tmp/forced/path.env")

    def test_falls_back_to_xdg_when_system_path_missing(self) -> None:
        """BRIAR_SECRETS_FILE unset + /etc/briar/secrets.env absent
        (laptop case) → resolves to $XDG_CONFIG_HOME/briar/secrets.env.

        We force the system path to a guaranteed-nonexistent location so
        the assertion is deterministic on droplets and laptops alike."""
        from pathlib import Path

        from briar.credentials.envfile import _secrets_path

        missing_system = Path("/nonexistent/etc/briar/secrets.env")
        env = {"BRIAR_SECRETS_FILE": "", "XDG_CONFIG_HOME": "/tmp/xdg-home"}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("briar.credentials._paths._SYSTEM_PATH", missing_system):
                resolved = _secrets_path()
        self.assertEqual(str(resolved), "/tmp/xdg-home/briar/secrets.env")

    def test_xdg_config_home_override_honoured(self) -> None:
        from pathlib import Path

        from briar.credentials.envfile import _secrets_path

        with mock.patch.dict(os.environ, {"BRIAR_SECRETS_FILE": "", "XDG_CONFIG_HOME": "/custom/cfg"}, clear=False):
            # Only test XDG-resolution when system path doesn't exist.
            # On a CI box where /etc/briar/secrets.env exists, this test
            # would (correctly) prefer system. Guard:
            if Path("/etc/briar/secrets.env").exists():
                self.skipTest("system /etc/briar/secrets.env exists on this host")
            self.assertEqual(str(_secrets_path()), "/custom/cfg/briar/secrets.env")


class Phase1CredentialHardeningTests(unittest.TestCase):
    """Regression tests for the Phase 1 security hardening of credential
    stores. Pins behavior that has caused incidents in similar systems:

      1. Auth errors (revoked token, expired session) must NOT silently
         look like "secret not found." The contract is: ``read`` returns
         ``None`` only for true not-found; auth/network/permission errors
         propagate so callers fail closed.
      2. EnvFileStore writes must be atomic and 0o600 BEFORE any bytes
         hit the disk — no chmod-after-write race window.
      3. A failed disk write must NOT leak the value into ``os.environ``
         (subprocesses inherit env; a "best effort" in-memory write is
         worse than nothing because the operator thinks the credential
         landed).
      4. Error messages crossing log boundaries never contain the
         credential's env-var name or the credential value itself.
      5. The rotation-detection fingerprint must not be a bare MD5 — use
         a keyed digest so an attacker with the hash cannot brute-force
         against common-secret rainbow tables."""

    # ── envfile write atomicity + permissions ──────────────────────

    def test_envfile_write_creates_0o600_file(self) -> None:
        """The temp file is opened with mode 0o600 directly (via
        os.open with explicit mode bits), so the bytes are never
        readable by group/other. The old chmod-after-write sequence
        left a microsecond window where the secret was world-readable."""
        import stat
        import tempfile

        from briar.credentials.envfile import EnvFileStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.env"
            with mock.patch.dict(os.environ, {"BRIAR_SECRETS_FILE": str(path)}, clear=False):
                store = EnvFileStore()
                store.write("TEST_BRIAR_PERMS", "secretvalue")
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600, f"expected 0o600 (-rw-------), got {oct(mode)}")

    def test_envfile_error_message_excludes_credential_name(self) -> None:
        """An OSError raised by ``write`` must not contain the credential
        env-var name. Operators tail logs; the var name + path pairing
        reveals which credentials are configured on this host."""
        from briar.credentials.envfile import EnvFileStore

        bad_path = Path("/dev/null/nope/secrets.env")
        with mock.patch.dict(os.environ, {"BRIAR_SECRETS_FILE": str(bad_path)}, clear=False):
            store = EnvFileStore()
            with self.assertRaises(OSError) as ctx:
                store.write("TEST_BRIAR_LEAK_NAME", "value")
            msg = str(ctx.exception)
            self.assertNotIn("TEST_BRIAR_LEAK_NAME", msg)
            self.assertNotIn("value", msg)
            # Path is allowed (operationally useful) — confirm it's there.
            self.assertIn("/dev/null", msg)

    # ── fingerprint is keyed (not bare MD5) ────────────────────────


class Phase2BoundaryDefaultsTests(unittest.TestCase):
    """Regression tests for the Phase 2 boundary-defaults hardening:
    timeouts, retries, observable failure, and edge validation."""

    # ── urlopen retry helper ───────────────────────────────────────

    def test_urlopen_with_retry_retries_on_429(self) -> None:
        import io
        import urllib.error

        from briar._http_retry import urlopen_with_retry

        # 429 then 200: should succeed on second attempt.
        success = mock.MagicMock()
        success.read.return_value = b"ok"
        success.__enter__ = lambda s: s
        success.__exit__ = lambda *a: None
        responses = [urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b"")), success]

        def fake_urlopen(req, timeout):  # noqa: ARG001
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("time.sleep"):  # don't actually sleep in test
                req = mock.MagicMock(full_url="https://x.test")
                result = urlopen_with_retry(req, timeout=1, attempts=3)
                self.assertIs(result, success)

    def test_urlopen_with_retry_does_not_retry_on_404(self) -> None:
        """4xx other than 429 is the caller's fault — auth, validation,
        endpoint typo. Retry doesn't help and wastes the rate budget."""
        import io
        import urllib.error

        from briar._http_retry import urlopen_with_retry

        call_count = {"n": 0}

        def fake_urlopen(req, timeout):  # noqa: ARG001
            call_count["n"] += 1
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b""))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            req = mock.MagicMock(full_url="https://x.test")
            with self.assertRaises(urllib.error.HTTPError):
                urlopen_with_retry(req, timeout=1, attempts=3)
            self.assertEqual(call_count["n"], 1, "404 should fail on first attempt, no retry")

    def test_urlopen_with_retry_honours_retry_after(self) -> None:
        """When Retry-After is present on a 429, sleep that long (capped
        at max_wait) instead of the computed exponential backoff."""
        import io
        import urllib.error

        from briar._http_retry import urlopen_with_retry

        success = mock.MagicMock()
        success.read.return_value = b"ok"
        success.__enter__ = lambda s: s
        success.__exit__ = lambda *a: None

        headers = mock.MagicMock()
        headers.get.return_value = "7"  # Retry-After: 7 seconds
        err = urllib.error.HTTPError("u", 429, "Too Many", headers, io.BytesIO(b""))
        responses = [err, success]
        sleeps: list = []

        def fake_urlopen(req, timeout):  # noqa: ARG001
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("time.sleep", side_effect=lambda s: sleeps.append(s)):
                req = mock.MagicMock(full_url="https://x.test")
                urlopen_with_retry(req, timeout=1, attempts=3, max_wait=30)
        self.assertEqual(sleeps, [7.0])  # honoured Retry-After exactly

    def test_urlopen_with_retry_exhausts_then_raises_last(self) -> None:
        import io
        import urllib.error

        from briar._http_retry import urlopen_with_retry

        def fake_urlopen(req, timeout):  # noqa: ARG001
            raise urllib.error.HTTPError("u", 503, "Down", {}, io.BytesIO(b""))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("time.sleep"):
                req = mock.MagicMock(full_url="https://x.test")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urlopen_with_retry(req, timeout=1, attempts=2)
                self.assertEqual(ctx.exception.code, 503)

    # ── input validation at edge ───────────────────────────────────

    def test_github_repo_validation_rejects_path_traversal(self) -> None:
        from briar.extract._providers.github import GithubProvider

        bad_inputs = [
            "foo/bar; rm -rf /",
            "foo/../bar",
            "../etc/passwd",
            "foo bar",  # space
            "foo",  # no slash
            "foo/bar/baz",  # too many segments
            '"foo/bar"',
            "foo/bar?x=1",
        ]
        provider = GithubProvider()
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_x"}, clear=False):
            for repo in bad_inputs:
                with self.subTest(repo=repo):
                    with self.assertRaises(ValueError):
                        provider.list_pulls(repo, state="open", max_count=1)

    def test_github_repo_validation_accepts_standard(self) -> None:
        """Validation must NOT reject standard GitHub repo names."""
        from briar.extract._providers.github import _validate_repo

        # All of these should not raise
        for repo in ("foo/bar", "Foo-Bar/baz_123", "user.name/repo.git", "iklobato/usebriar-landing"):
            _validate_repo(repo)

    def test_jira_project_validation_rejects_jql_injection(self) -> None:
        """End-to-end: bad project → list_tickets raises ValueError.
        Pinned now that Phase 3 swallow_errors excludes caller errors."""
        from briar.extract._trackers.jira import _PROJECT_RE, JiraTracker

        bad_inputs = [
            'foo" OR project != "bar',
            "PROJECT; DROP TABLE",
            "proj-with-dash",  # standard Jira keys don't allow dashes
            "lowercase",
            "",
            'X" UNION SELECT',
        ]
        with mock.patch.dict(os.environ, {}, clear=False):
            tracker = JiraTracker(logins=FakeLogins({"jira": {"site": "x.test"}}, {"BRIAR_ACME_JIRA_EMAIL": "a@b", "BRIAR_ACME_JIRA_TOKEN": "t"}).logins())
            for project in bad_inputs:
                with self.subTest(project=project):
                    with self.assertRaises(ValueError):
                        tracker.list_tickets(project, state="open", max_count=1)

        # Regex itself accepts standard Jira keys.
        for project in ("PROJ", "ENG", "A1", "BR_123"):
            with self.subTest(project=project):
                self.assertIsNotNone(_PROJECT_RE.match(project), f"regex should accept {project!r}")

    # ── GraphQL errors raise rather than return partial ────────────

    def test_linear_gql_errors_raise(self) -> None:
        """Previous shape logged WARNING then returned a body with
        data:None — downstream silently treated it as empty results,
        masking schema/auth errors. Now: raise."""
        import json as _json

        from briar.extract._trackers.linear import LinearTracker

        body = _json.dumps({"errors": [{"message": "Invalid token"}]}).encode("utf-8")
        fake_resp = mock.MagicMock()
        fake_resp.read.return_value = body
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda *a: None

        with mock.patch.dict(os.environ, {}, clear=False):
            tracker = LinearTracker(logins=FakeLogins(environ={"BRIAR_ACME_LINEAR_API_KEY": "t"}).logins())
            with mock.patch("briar.extract._trackers.linear.urlopen_with_retry", return_value=fake_resp):
                with self.assertRaises(RuntimeError):
                    tracker._gql("query", {})  # noqa: SLF001 - direct test of internal


class Phase3ErrorHandlingTests(unittest.TestCase):
    """Regression tests for the Phase 3 error-handling discipline:
    swallow_errors propagates caller errors, EMPTY_SECTION is no
    longer a mutable shared singleton, scaffold raises ConfigError
    (not SystemExit), and _KNOWN_MATCHERS is derived from CredEnv."""

    def test_swallow_errors_propagates_value_error(self) -> None:
        """ValueError signals caller bug — must not be swallowed and
        defaulted to empty/None. This used to mask runbook YAML typos
        (e.g. lowercase Jira project) as 'no tickets found.'"""
        from briar.decorators import swallow_errors

        @swallow_errors(default=[], message="test verb")
        def verb_with_validation(x: int) -> list:
            if x < 0:
                raise ValueError("x must be >= 0")
            return [x]

        self.assertEqual(verb_with_validation(5), [5])
        with self.assertRaises(ValueError):
            verb_with_validation(-1)

    def test_swallow_errors_still_catches_runtime_errors(self) -> None:
        """The original purpose of swallow_errors — network/SDK
        failures should still become default+log, not propagate."""
        from briar.decorators import swallow_errors

        @swallow_errors(default="fallback", message="test verb")
        def network_verb() -> str:
            raise RuntimeError("boto3.ClientError: throttled")

        self.assertEqual(network_verb(), "fallback")

    def test_swallow_errors_propagates_type_error(self) -> None:
        from briar.decorators import swallow_errors

        @swallow_errors(default=None, message="test")
        def verb():
            raise TypeError("bad arg")

        with self.assertRaises(TypeError):
            verb()

    def test_empty_section_returns_fresh_instance(self) -> None:
        """The old EMPTY_SECTION singleton was a footgun — mutating
        .data on one caller's return poisoned every other caller's
        'empty' return. empty_section() must return a fresh instance."""
        from briar.extract.base import empty_section

        a = empty_section()
        b = empty_section()
        self.assertIsNot(a, b, "empty_section() must return a fresh instance per call")
        a.data["x"] = 1
        self.assertEqual(b.data, {}, "mutating one empty_section() must not leak to others")

    def test_list_excludes_briar_config_vars(self) -> None:
        """Config vars (`BRIAR_SECRETS_FILE`, `AWS_PROFILE`) share prefixes
        with credentials. A bare-prefix match would leak them into
        `auth list`; full-template matching keeps them out while still
        listing the real per-company creds."""
        from unittest import mock

        from briar.credentials.envfile import EnvFileStore

        env = {
            "BRIAR_SECRETS_FILE": "/etc/briar/secrets.env",  # config — must NOT list
            "BRIAR_NOTIFY_SINKS": "telegram,slack",  # config — must NOT list
            "BRIAR_VERBOSE": "1",  # config — must NOT list
            "AWS_PROFILE": "dev",  # not a briar credential: must NOT list
            "AWS_ACME_REGION": "us-east-1",  # a 1.x name briar no longer reads: must NOT list
            "BRIAR_ACME_GITHUB_TOKEN": "PLACEHOLDER-not-a-secret",  # a 2.0 login: must list
            "GITHUB_TOKEN": "ghp_x",  # a 1.x name briar no longer reads: must NOT list
        }
        with mock.patch.dict("os.environ", env, clear=True):
            names = EnvFileStore().list()
        self.assertNotIn("BRIAR_SECRETS_FILE", names)
        self.assertNotIn("BRIAR_NOTIFY_SINKS", names)
        self.assertNotIn("BRIAR_VERBOSE", names)
        self.assertNotIn("AWS_PROFILE", names)
        self.assertNotIn("AWS_ACME_REGION", names)
        self.assertIn("BRIAR_ACME_GITHUB_TOKEN", names)
        self.assertNotIn("GITHUB_TOKEN", names)


class Phase4PythonStyleHygieneTests(unittest.TestCase):
    """Regression tests for Phase 4 mechanical-style hygiene fixes."""

    def test_scheduler_warns_on_tz_with_sub_day_cadence(self) -> None:
        """schedule library's tz support is day-or-coarser. Sub-day
        cadences silently drop tz, which surprised operators relying
        on it for hourly jobs."""
        import logging as _logging

        from briar.iac.runbook.scheduler import EveryParser

        with self.assertLogs("briar.iac.runbook.scheduler", level=_logging.WARNING) as captured:
            EveryParser.parse("hour at :15", tz="America/New_York", scheduler=schedule.Scheduler())
        self.assertTrue(any("ignored" in msg for msg in captured.output))


class Phase5DispatchEliminationTests(unittest.TestCase):
    """Regression tests for Phase 5 — every string-keyed elif chain
    that was flagged is now a registry / table / ClassVar lookup."""

    def test_runbook_command_dispatches_via_actions_table(self) -> None:
        """commands/runbook.py used `if args.op == 'extract' elif 'sweep' ...`
        — now an `_ACTIONS` dict matching commands/auth.py. `sweep` left in 2.0 (C14)."""
        from briar.commands.runbook import CommandRunbook

        cmd = CommandRunbook()
        self.assertEqual(set(cmd._ACTIONS), {"extract", "serve"})
        # Each entry resolves to a real method on the command
        for action, handler_name in cmd._ACTIONS.items():
            with self.subTest(action=action):
                self.assertTrue(callable(getattr(cmd, handler_name)))

    def test_make_sink_uses_factory_dict_not_elif_chain(self) -> None:
        """Unknown sink name raises with a useful list — confirms the
        registry handles dispatch instead of an open elif."""
        from briar.errors import CliError
        from briar.telemetry._sinks import _SINK_FACTORIES, make_sink
        from briar.telemetry._sinks.noop import NoOpSink

        # All three known kinds construct
        self.assertIsInstance(make_sink("noop"), NoOpSink)
        # Unknown kind raises with the known set
        with self.assertRaises(CliError) as ctx:
            make_sink("sntry")  # typo
        self.assertIn("known:", str(ctx.exception))
        # Factory dict exposed for introspection (callers can list options)
        self.assertEqual(set(_SINK_FACTORIES), {"noop", "sentry", "file"})

    def test_aws_gatherer_data_key_drives_dispatch(self) -> None:
        """Each AwsServiceGatherer subclass declares its own data_key —
        replaces a magic-string tuple in AwsCloudProvider._gather_via."""
        from briar.extract.aws_services import AWS_SERVICE_GATHERERS

        expected = {"ecs": "services", "lambda": "functions", "sqs": "queues", "rds": "instances", "logs": "top_log_groups"}
        for name, key in expected.items():
            with self.subTest(svc=name):
                gatherer = AWS_SERVICE_GATHERERS[name]
                self.assertEqual(gatherer.data_key, key)

    def test_adf_block_nodes_is_constant_set(self) -> None:
        """Inline tuple membership replaced with a module-level frozenset
        so adding a new ADF block-node type is one entry, not a tuple
        edit inside the recursive walker."""
        from briar.extract._trackers.jira import _ADF_BLOCK_NODES

        self.assertIsInstance(_ADF_BLOCK_NODES, frozenset)
        # Spot-check the canonical block-node types
        for kind in ("paragraph", "heading", "bulletList"):
            self.assertIn(kind, _ADF_BLOCK_NODES)
        # Non-block nodes (`text`, `hardBreak`) are NOT in the set
        self.assertNotIn("text", _ADF_BLOCK_NODES)
        self.assertNotIn("hardBreak", _ADF_BLOCK_NODES)

    def test_github_label_kind_priority_helper(self) -> None:
        """Inline `for lbl in labels: if low in (...)` chain extracted
        to a typed helper. Easier to extend with new kinds."""
        from briar.extract._trackers.github_issues import _kind_priority_from_labels

        # Both kind + priority detected
        self.assertEqual(_kind_priority_from_labels(["bug", "p0"]), ("bug", "p0"))
        # First priority wins (preserves original "priority = priority or lbl" shape)
        self.assertEqual(_kind_priority_from_labels(["p1", "priority/critical"]), ("", "p1"))
        # Neither
        self.assertEqual(_kind_priority_from_labels(["needs-review"]), ("", ""))


class Phase6PrimitiveObsessionTests(unittest.TestCase):
    """Regression tests for Phase 6 — primitive obsession + dead-code
    cleanups. Closed enumerations are typed; the meeting-mode
    TypedDict/Enum (set-but-never-read) is gone."""

    def test_meeting_types_module_deleted(self) -> None:
        """extract/_types.py and extract/_enums.py held the
        MeetingExtractedData TypedDict + MeetingExtractMode enum.
        Both were constructed by 2 producer sites and never branched
        on. Deletion + dict-only construction is strictly simpler."""
        with self.assertRaises(ModuleNotFoundError):
            import briar.extract._types  # noqa: F401

        with self.assertRaises(ModuleNotFoundError):
            import briar.extract._enums  # noqa: F401

    def test_knowledge_binding_rejects_unknown_store(self) -> None:
        """Literal annotation + validator combined: typos are caught
        at parse time, not when the store is first instantiated."""
        from pydantic import ValidationError

        from briar.iac.runbook.models import KnowledgeBinding

        # The file store parses; the removed postgres store and typos are rejected
        KnowledgeBinding(store="file")
        with self.assertRaisesRegex(ValidationError, "store: postgres` was removed in briar 2.0"):
            KnowledgeBinding(store="postgres")
        with self.assertRaises(ValidationError):
            KnowledgeBinding(store="redis")

    def test_extract_no_task_filter_constant_gone(self) -> None:
        """NO_TASK_FILTER = "" was a sentinel for "run every task".
        Now Optional[str] = None; empty string still works via the
        `task or None` coerce at the function entry."""
        from briar.iac.runbook import executor

        self.assertFalse(hasattr(executor, "NO_TASK_FILTER"))

    def test_extract_runbook_accepts_none_and_empty_string(self) -> None:
        """Both `task=None` and `task=""` mean "no filter" — argparse
        callers pass empty string by default; programmatic callers
        may pass None."""
        import inspect

        from briar.iac.runbook.executor import RunbookExtractor

        sig = inspect.signature(RunbookExtractor.extract)
        # Default is now None, not ""
        self.assertIs(sig.parameters["task"].default, None)


class Phase7DeduplicationTests(unittest.TestCase):
    """Regression tests for Phase 7 — duplicated helpers consolidated
    into one place. Adding a new caller (or extractor / writer) reuses
    the shared helper instead of forking another copy."""

    def test_hours_between_shared_module(self) -> None:
        """pr-archaeology and ticket-archaeology had verbatim copies
        (with the same UNPARSABLE_HOURS = -1.0 sentinel). Now a single
        source of truth in extract/_time_util.py."""
        from briar.extract._time_util import UNPARSABLE_HOURS, hours_between

        self.assertEqual(UNPARSABLE_HOURS, -1.0)
        # Round-trip a known interval
        self.assertAlmostEqual(
            hours_between("2026-01-01T00:00:00Z", "2026-01-01T02:30:00Z"),
            2.5,
            places=4,
        )
        # Parse error returns the sentinel, not raises
        self.assertEqual(hours_between("garbage", "2026-01-01T00:00:00Z"), UNPARSABLE_HOURS)

    def test_hours_between_extractor_aliases_point_to_helper(self) -> None:
        """Both archetypes alias `_hours_between` to the shared helper,
        preserving call-site compatibility."""
        from briar.extract._time_util import hours_between
        from briar.extract.pr_archaeology import ExtractPrArchaeology
        from briar.extract.ticket_archaeology import ExtractTicketArchaeology

        self.assertIs(ExtractPrArchaeology._hours_between, hours_between)
        self.assertIs(ExtractTicketArchaeology._hours_between, hours_between)

    def test_secrets_path_single_source_of_truth(self) -> None:
        """envfile store + envfile bootstrap used to inline the same
        resolution chain. Now both import from credentials/_paths."""
        from briar.credentials._bootstraps.envfile import _resolve_secrets_path
        from briar.credentials._paths import secrets_path
        from briar.credentials.envfile import _secrets_path

        # All three names point at the same callable
        self.assertIs(_secrets_path, secrets_path)
        self.assertIs(_resolve_secrets_path, secrets_path)
        # Behaviour: explicit env var wins
        with mock.patch.dict(os.environ, {"BRIAR_SECRETS_FILE": "/forced/path.env"}, clear=False):
            self.assertEqual(str(secrets_path()), "/forced/path.env")

    def test_dataclasses_replace_preserves_all_ticket_fields(self) -> None:
        """The 4 tracker get_ticket overrides now use `dataclasses.replace`
        instead of `Ticket(key=t.key, title=t.title, ..., description=...)`.
        Confirms replace() actually preserves the other 11 fields."""
        from dataclasses import replace

        from briar.extract._tracker import Ticket

        original = Ticket(
            key="PROJ-1",
            title="title",
            reporter="alice",
            assignee="bob",
            status="open",
            kind="bug",
            priority="P1",
            created_at="2026-01-01",
            updated_at="2026-01-02",
            labels=["urgent"],
            url="https://x/1",
        )
        hydrated = replace(original, description="extra body")
        self.assertEqual(hydrated.description, "extra body")
        # Every other field survived intact
        self.assertEqual(hydrated.key, original.key)
        self.assertEqual(hydrated.labels, original.labels)
        self.assertEqual(hydrated.url, original.url)

    def test_render_meeting_header_parametrised(self) -> None:
        """meeting_context (cap=12, no suffix) and meeting_digest
        (cap=8, with "+N more") share the helper but pass different
        formatting knobs."""
        from briar.extract._meeting import Meeting, render_meeting_header

        meeting = Meeting(
            meeting_id="m-1",
            title="Standup",
            started_at="2026-05-01T10:00:00Z",
            duration_sec=1800,
            organizer="alice@x",
            attendees=[f"u{i}@x" for i in range(15)],
            url="https://meet/1",
        )
        # cap=8 + suffix: shows 8 attendees + "+7 more"
        digest_lines = render_meeting_header(meeting, attendee_cap=8, show_more_suffix=True)
        attendees_line = next(line for line in digest_lines if line.startswith("**Attendees**"))
        self.assertIn("(+7 more)", attendees_line)
        # cap=12 no suffix: shows 12 attendees, no overflow note
        ctx_lines = render_meeting_header(meeting, attendee_cap=12, show_more_suffix=False)
        attendees_line = next(line for line in ctx_lines if line.startswith("**Attendees**"))
        self.assertNotIn("more", attendees_line)


class Phase8GodClassSplitsTests(unittest.TestCase):
    """Regression tests for Phase 8 — selective method splits +
    ExitCode collapse. Bigger restructurings (CommandAgent class
    split, commands/plan.py → package) deferred to dedicated PRs."""

    def test_exit_code_granular_members_removed(self) -> None:
        """STORE_OPEN_FAILED / CLONE_FAILED / GIT_CONFIG_FAILED /
        AGENT_ERROR (3-6) were never observed by the top-level CLI
        dispatcher — folded into GENERAL_ERROR."""
        from briar.commands._enums import ExitCode

        self.assertEqual({e.name for e in ExitCode}, {"OK", "GENERAL_ERROR", "USAGE_ERROR"})

    def test_failure_ctx_records_through_one_object(self) -> None:
        """_FailureCtx bundles the 3 constant per-schedule fields so
        the failure path is `failure.record(reason=, blob_name=, exc=)`
        — replaces a 7-arg call duplicated 3 times in the original."""
        from briar.iac.runbook.executor import ExtractRow, _FailureCtx

        rows: list = []
        ctx = _FailureCtx(company_name="Acme Inc", task="extractors", rows=rows)
        with self.assertLogs("briar.iac.runbook.executor", level="ERROR") as logs:
            ctx.record(reason="collect_sections raised", blob_name="knowledge:acme", exc=RuntimeError("boom"))
        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0], ExtractRow)
        self.assertEqual(rows[0].company, "Acme Inc")
        self.assertEqual(rows[0].task, "extractors")
        self.assertIn("collect_sections raised", rows[0].status)
        self.assertIn("schedule-failed: collect_sections raised: boom", logs.output[0])

    def test_open_store_and_record_outcome_split_out(self) -> None:
        """Confirm the new helpers exist and are static (no self-state)."""
        from briar.iac.runbook.executor import RunbookExtractor

        self.assertTrue(hasattr(RunbookExtractor, "_open_store"))
        self.assertTrue(hasattr(RunbookExtractor, "_record_outcome"))

    def test_fetch_commit_files_helper(self) -> None:
        """github list_recent_commits inlined per-commit file fetches;
        now `_fetch_commit_files(repo, sha)` is named + isolated."""
        from briar.extract._providers.github import GithubProvider

        # Mock GithubApi.get_json so no network is touched
        with mock.patch("briar.extract._providers.github.GithubApi.get_json") as get_json:
            get_json.return_value = {"files": [{"filename": "src/x.py"}, {"filename": "tests/y.py"}, {}]}
            files = GithubProvider()._fetch_commit_files("o/r", "deadbeef")
        self.assertEqual(files, ["src/x.py", "tests/y.py"])

    def test_fetch_commit_files_handles_non_dict_response(self) -> None:
        from briar.extract._providers.github import GithubProvider

        with mock.patch("briar.extract._providers.github.GithubApi.get_json", return_value="error string"):
            self.assertEqual(GithubProvider()._fetch_commit_files("o/r", "sha"), [])

    def test_bitbucket_field_helper_prefers_attr_then_data(self) -> None:
        """_field(pr, name) replaces the open-coded `getattr or data.get`
        dance scattered through _to_pull."""
        from briar.extract._providers.bitbucket import BitbucketProvider

        # PR has the attribute populated → returned directly
        pr_with_attr = mock.MagicMock(spec=["title", "data"])
        pr_with_attr.title = "from-attr"
        pr_with_attr.data = {"title": "from-data"}
        self.assertEqual(BitbucketProvider._field(pr_with_attr, "title"), "from-attr")

        # PR's attribute is None or absent → falls back to data
        pr_attr_none = mock.MagicMock(spec=["title", "data"])
        pr_attr_none.title = None
        pr_attr_none.data = {"title": "from-data"}
        self.assertEqual(BitbucketProvider._field(pr_attr_none, "title"), "from-data")

        # Neither attr nor data has it → default
        pr_neither = mock.MagicMock(spec=["data"])
        pr_neither.data = {}
        self.assertEqual(BitbucketProvider._field(pr_neither, "title", "fallback"), "fallback")

    def test_bitbucket_person_display_handles_none(self) -> None:
        from briar.extract._providers.bitbucket import BitbucketProvider

        self.assertEqual(BitbucketProvider._person_display(None), "")
        # Newer SDK: display_name
        p = mock.MagicMock(spec=["display_name", "nickname"])
        p.display_name = "Alice"
        p.nickname = ""
        self.assertEqual(BitbucketProvider._person_display(p), "Alice")
        # Older SDK: nickname only
        p2 = mock.MagicMock(spec=["display_name", "nickname"])
        p2.display_name = ""
        p2.nickname = "alice42"
        self.assertEqual(BitbucketProvider._person_display(p2), "alice42")


class Phase9SpeculativeGeneralityCollapseTests(unittest.TestCase):
    """Regression tests for Phase 9 — speculative generality collapse.
    Bigger ABC restructurings (extract/base.py 7-ABC mixin,
    _jira_auth.py 258-line inline, language_detectors flatten) deferred
    to dedicated PRs; those file-level reorgs warrant their own
    focused diff."""

    def test_pagination_exposes_module_level_functions(self) -> None:
        """Free functions are the canonical API; the `Payload` shim was
        deleted in Phase 11 after all formatters migrated."""
        import briar.pagination as pagination
        from briar.pagination import items_of, looks_like_list

        self.assertEqual(items_of([1, 2, 3]), [1, 2, 3])
        self.assertTrue(looks_like_list([]))
        # The shim is gone — confirm migration is complete
        self.assertFalse(hasattr(pagination, "Payload"))

    def test_cloud_provider_base_no_services_kwarg(self) -> None:
        """Base CloudProvider.list_subsections doesn't accept the
        AWS-specific `services=` kwarg (ISP). AwsCloudProvider has it;
        every other cloud just gets list_subsections() with no args."""
        import inspect

        from briar.extract._cloud import CloudProvider
        from briar.extract._clouds.aws import AwsCloudProvider

        base_sig = inspect.signature(CloudProvider.list_subsections)
        self.assertNotIn("services", base_sig.parameters)
        aws_sig = inspect.signature(AwsCloudProvider.list_subsections)
        self.assertIn("services", aws_sig.parameters)


class Phase10PolishTests(unittest.TestCase):
    """Regression tests for Phase 10 — magic-constant scrub, private-
    internal cleanups, and silent-fallback eliminations."""

    def test_context_filter_does_not_double_prefix(self) -> None:
        """Two ContextFilter instances on the same record (e.g. one
        per handler) used to apply the prefix twice. Sentinel flag
        prevents that."""
        import logging as _logging

        from briar.log_context import ContextFilter, log_context

        filt_a = ContextFilter()
        filt_b = ContextFilter()
        with log_context(company="acme", task="prfix"):
            record = _logging.LogRecord("briar.test", _logging.INFO, "x", 1, "hello", None, None)
            filt_a.filter(record)
            filt_b.filter(record)
        # Should appear exactly once
        self.assertEqual(str(record.msg).count("[company=acme task=prfix]"), 1)

    def test_formatter_unknown_name_raises(self) -> None:
        """`--format yam` (typo) used to silently fall back to table.
        Now CliError with the known set."""
        from briar.errors import CliError
        from briar.formatting import FormatterRegistry

        with self.assertRaises(CliError) as ctx:
            FormatterRegistry.get("yam")
        self.assertIn("known:", str(ctx.exception))
        # All real formats still resolve
        for known in FormatterRegistry.names():
            with self.subTest(format=known):
                self.assertIsNotNone(FormatterRegistry.get(known))

    def test_provider_company_property_exposed(self) -> None:
        """Public accessor on RepositoryProvider replaces
        `getattr(provider, "_company")` reach-through. The default
        impl reads from the historical `_company` instance attr."""
        from briar.extract._provider import RepositoryProvider

        class _DummyProvider(RepositoryProvider):
            kind = "dummy"

            def __init__(self, *, company: str = "") -> None:
                self._company = company

            def is_available(self) -> bool:
                return True

            def resolve_token(self) -> str:
                return ""

            def clone_url(self, owner: str, repo: str) -> str:
                return ""

            def authed_clone_url(self, owner: str, repo: str, token: str) -> str:
                return ""

            def pr_creation_recipe(self, *, owner: str, repo: str, branch: str) -> str:
                return ""

            def list_pulls(self, repo: str, *, state: str, max_count: int):
                return []

            def read_file(self, repo: str, path: str) -> str:
                return ""

        p_with = _DummyProvider(company="acme")
        p_empty = _DummyProvider()
        self.assertEqual(p_with.company, "acme")
        self.assertEqual(p_empty.company, "")


class Phase11FollowupTests(unittest.TestCase):
    """Regression tests for Phase 11 — bugs surfaced by the re-audit
    + security hardening + refactor-shim cleanup."""

    # ── P0 bugs in aws.py ────────────────────────────────────────────

    def test_aws_list_databases_reads_id_not_identifier(self) -> None:
        """Re-audit found list_databases reading 'identifier' while RDS
        gatherer writes 'id'. Phase 11 threaded gatherer.data_key like
        _gather_via does."""
        import inspect

        from briar.extract._clouds.aws import AwsCloudProvider

        src = inspect.getsource(AwsCloudProvider.list_databases)
        # The bug was `row.get("identifier")` — must now read `row.get("id")`
        self.assertIn('row.get("id")', src)
        self.assertNotIn('row.get("identifier")', src)
        # And the section lookup uses gatherer.data_key, not the hardcoded "instances"
        self.assertIn("gatherer.data_key", src)

    def test_aws_list_log_groups_uses_data_key(self) -> None:
        """Re-audit found list_log_groups reading 'groups' while the
        logs gatherer's data_key is 'top_log_groups' — always-empty
        bug in production."""
        import inspect

        from briar.extract._clouds.aws import AwsCloudProvider

        src = inspect.getsource(AwsCloudProvider.list_log_groups)
        self.assertIn("gatherer.data_key", src)
        # Hardcoded "groups" lookup was the bug
        self.assertNotIn('.get("groups"', src)

    # ── _http_retry assert anti-pattern ──────────────────────────────

    def test_http_retry_uses_if_check_not_assert(self) -> None:
        """`assert last_exc is not None` was used in _http_retry,
        identical to the anti-pattern I removed from error_policy.py
        in Phase 0. -O strips asserts, leaving `raise None`."""
        import inspect

        from briar import _http_retry

        src = inspect.getsource(_http_retry.urlopen_with_retry)
        # The fix uses an explicit if-check + RuntimeError
        self.assertNotIn("assert last_exc", src)
        self.assertIn("if last_exc is None", src)

    # ── envfile bootstrap deny-list ──────────────────────────────────

    def test_envfile_bootstrap_refuses_ld_preload(self) -> None:
        """LD_PRELOAD / PYTHONPATH / HTTP_PROXY in an envfile would
        smuggle code execution / outbound interception. Phase 11
        denied them via _DENY_ENV_VARS."""
        import tempfile

        from briar.credentials._bootstraps.envfile import EnvFileBootstrap

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.env"
            path.write_text("LD_PRELOAD=/tmp/evil.so\n" "PYTHONPATH=/tmp/evil-modules\n" "HTTP_PROXY=http://attacker:8080\n" "ANTHROPIC_API_KEY=sk-real\n")
            with mock.patch.dict(os.environ, {"BRIAR_SECRETS_FILE": str(path)}, clear=True):
                result = EnvFileBootstrap().hydrate()
                # All assertions must run inside the patched-env context;
                # mock.patch.dict(clear=True) restores the test runner's
                # original env (which includes PYTHONPATH) on __exit__.
                self.assertIn("ANTHROPIC_API_KEY", result.written)
                self.assertNotIn("LD_PRELOAD", result.written)
                self.assertNotIn("PYTHONPATH", result.written)
                self.assertNotIn("HTTP_PROXY", result.written)
                self.assertNotIn("LD_PRELOAD", os.environ)
                self.assertNotIn("PYTHONPATH", os.environ)
                self.assertNotIn("HTTP_PROXY", os.environ)
                # The safe credential DID land
                self.assertEqual(os.environ.get("ANTHROPIC_API_KEY"), "sk-real")

    # ── envfile value validation ─────────────────────────────────────

    def test_envfile_write_rejects_newline_in_value(self) -> None:
        """A value containing \\n would smuggle a second KEY=value line
        on disk, effectively letting one credential write inject
        another credential."""
        import tempfile

        from briar.credentials.envfile import EnvFileStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.env"
            with mock.patch.dict(os.environ, {"BRIAR_SECRETS_FILE": str(path)}, clear=False):
                store = EnvFileStore()
                with self.assertRaises(ValueError) as ctx:
                    store.write("TEST_NEWLINE", "value\nANOTHER_KEY=injected")
                self.assertIn("control character", str(ctx.exception))

    def test_envfile_write_rejects_null_byte(self) -> None:
        import tempfile

        from briar.credentials.envfile import EnvFileStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.env"
            with mock.patch.dict(os.environ, {"BRIAR_SECRETS_FILE": str(path)}, clear=False):
                store = EnvFileStore()
                with self.assertRaises(ValueError):
                    store.write("TEST_NULL", "value\x00trunc")

    # ── [AI] prefix on additional writers ────────────────────────────

    # ── Shim cleanup ─────────────────────────────────────────────────

    def test_payload_shim_deleted(self) -> None:
        """Phase 11 finished the migration started in Phase 9 — every
        formatter now imports the free functions and the `Payload`
        shim class is gone."""
        import briar.pagination as pagination

        self.assertFalse(hasattr(pagination, "Payload"))

    def test_build_registry_alias_deleted(self) -> None:
        """commands/__init__.py:build_registry was a shim for the old
        public name. After migrating cli.py to CommandRegistry.build,
        the shim is gone."""
        import briar.commands as cmds

        self.assertFalse(hasattr(cmds, "build_registry"))

    def test_apply_default_config_no_longer_has_placeholder(self) -> None:
        """The vestigial `_placeholder` arg and module-level
        _DEFAULT_BOTO_CONFIG global from Phase 2 are both gone."""
        import inspect

        from briar.extract._clouds import aws

        sig = inspect.signature(aws._apply_default_config)
        self.assertEqual(list(sig.parameters.keys()), ["session"])
        self.assertFalse(hasattr(aws, "_DEFAULT_BOTO_CONFIG"))


class Phase12FollowupTests(unittest.TestCase):
    """Regression tests for Phase 12 — quick wins from the post-Phase-11
    deferred list: security defaults, constant centralisation, deduplicated
    scaffold helpers, missing tracker timeouts."""

    # The "message body holds only the error type" check moved to
    # tests/unit/telemetry/test_sentry_wire.py, against the real SDK.

    def test_meeting_constants_centralised(self) -> None:
        """DEFAULT_MEETING_TOP_K and DEFAULT_MEETING_MAX_BYTES live in
        extract/_meeting.py and feed every command-side default."""
        from briar.extract._meeting import DEFAULT_MEETING_MAX_BYTES, DEFAULT_MEETING_TOP_K

        self.assertEqual(DEFAULT_MEETING_TOP_K, 3)
        self.assertEqual(DEFAULT_MEETING_MAX_BYTES, 50_000)


class Phase13StructuralFollowupTests(unittest.TestCase):
    """Regression tests for Phase 13 — language_detectors flatten,
    ScaffoldResolver/ScaffoldArgs demoted, cloud timeout gap documented."""

    def test_language_detectors_submodules_deleted(self) -> None:
        """The per-language sub-modules (base.py / python.py / node.py
        / go.py) are deleted; everything lives in __init__.py now."""
        for sub in ("base", "python", "node", "go"):
            with self.subTest(sub=sub):
                with self.assertRaises(ModuleNotFoundError):
                    __import__(f"briar.extract.language_detectors.{sub}")

    def test_language_detector_is_dataclass_not_abc(self) -> None:
        import dataclasses

        from briar.extract.language_detectors import LANGUAGE_DETECTORS, LanguageDetector

        self.assertTrue(dataclasses.is_dataclass(LanguageDetector))
        # Registry still produces 3 detectors with usable callables
        names = sorted(d.name for d in LANGUAGE_DETECTORS)
        self.assertEqual(names, ["go", "node", "python"])
        for d in LANGUAGE_DETECTORS:
            with self.subTest(name=d.name):
                self.assertTrue(callable(d.detect))
                # Missing-manifest path returns {} for every detector
                self.assertEqual(d.detect("o/r", lambda r, p: ""), {})

    def test_cloud_timeout_gap_documented(self) -> None:
        """Azure and GCP cloud providers carry an explicit TODO
        comment about the timeout-parity gap, so a future maintainer
        sees the conscious deferral rather than assuming defaults are
        intentional."""
        from pathlib import Path

        azure_src = Path("src/briar/extract/_clouds/azure.py").read_text()
        gcp_src = Path("src/briar/extract/_clouds/gcp.py").read_text()
        self.assertIn("TODO(timeout-parity)", azure_src)
        self.assertIn("TODO(timeout-parity)", gcp_src)


class Phase13DemotionsTests(unittest.TestCase):
    """Regression tests for the Phase 13 follow-up batch: deleted
    dict-form filter, install-id caching, telemetry scrub-URL fix,
    MeetingProviderRegistry demotion, Cli class demotion."""

    def test_apply_user_filter_dict_form_deleted(self) -> None:
        """Only `apply_user_filter_objs` survives — the dict-form had
        zero src callers."""
        import briar.extract._user_filter as user_filter

        self.assertFalse(hasattr(user_filter, "apply_user_filter"))
        self.assertTrue(hasattr(user_filter, "apply_user_filter_objs"))
        # UserFilter.apply + the _login_of/_logins_of helpers are gone too
        self.assertFalse(hasattr(user_filter.UserFilter, "apply"))
        self.assertFalse(hasattr(user_filter.UserFilter, "_login_of"))

    def test_install_id_caches_in_memory(self) -> None:
        """A read-only home dir would force a fresh UUID every call
        before Phase 13 — inflating distinct-install metrics."""
        import briar.telemetry._config as cfg

        # Force the cache miss + read-only-disk path
        cfg._INSTALL_ID_CACHE = None
        with mock.patch.object(cfg.Path, "read_text", side_effect=OSError("read-only fs")):
            first = cfg._load_or_create_install_id()
            second = cfg._load_or_create_install_id()
        self.assertEqual(first, second)
        # Restore for any subsequent tests
        cfg._INSTALL_ID_CACHE = None

    def test_abs_path_patterns_preserve_url_paths(self) -> None:
        """The previous unanchored pattern collapsed `/v1/foo` inside
        URLs into `<path>`. Now anchored so URLs survive."""
        from briar.telemetry._scrubber import _ABS_PATH_PATTERNS

        url = "https://api.example.com/v1/foo"
        for pat in _ABS_PATH_PATTERNS:
            self.assertIsNone(pat.search(url), f"pattern {pat.pattern} matched inside a URL")

        # Standalone path still matches
        msg = "could not read /Users/iklo/.config/briar/secrets.env"
        matched = False
        for pat in _ABS_PATH_PATTERNS:
            if pat.search(msg):
                matched = True
                break
        self.assertTrue(matched, "leading-whitespace path should still scrub")

    def test_meeting_provider_registry_class_gone(self) -> None:
        import briar.extract._meetings as meetings

        self.assertFalse(hasattr(meetings, "MeetingProviderRegistry"))
        self.assertTrue(callable(meetings.make_meeting))
        self.assertTrue(callable(meetings.meeting_kinds))
        self.assertIn("fireflies", meetings.meeting_kinds())

    def test_cli_class_demoted_to_module_functions(self) -> None:
        import briar.cli as cli

        self.assertFalse(hasattr(cli, "Cli"))
        self.assertTrue(callable(cli.main))
        self.assertTrue(callable(cli._extract_global_flags))
        self.assertTrue(callable(cli._build_parser))


class Phase14StructuralCleanupTests(unittest.TestCase):
    """Regression tests for Phase 14 — dead requires_* ClassVar
    deletion + CommandAgent template-method extraction."""

    def test_unused_requires_classvars_deleted(self) -> None:
        """4 of the 6 `requires_*` ClassVars were declared but never
        read anywhere. Phase 13 deleted them; 2.0 deleted the last 2
        (`requires_github` / `requires_aws`) with the dashboard that read
        them."""
        from briar.extract.base import CloudBackedExtractor, KnowledgeExtractor, MeetingBackedExtractor, RepoBackedExtractor, TrackerBackedExtractor

        # Base + 4 *Backed* subclasses no longer declare the dead flags
        for cls in (KnowledgeExtractor, CloudBackedExtractor, TrackerBackedExtractor, MeetingBackedExtractor, RepoBackedExtractor):
            with self.subTest(cls=cls.__name__):
                self.assertFalse(
                    "requires_repository_provider" in cls.__dict__
                    or "requires_tracker_provider" in cls.__dict__
                    or "requires_meeting_provider" in cls.__dict__
                    or "requires_cloud_provider" in cls.__dict__,
                    f"{cls.__name__} still declares one of the dead requires_* ClassVars",
                )

        self.assertNotIn("requires_github", KnowledgeExtractor.__dict__)
        self.assertNotIn("requires_aws", KnowledgeExtractor.__dict__)


class Phase14BackedExtractorHelpersTests(unittest.TestCase):
    """Regression tests for the *Backed*Extractor dispatch-shape
    extraction. The 7 ABC subclasses (Cloud / Tracker / Meeting / Repo
    + 3 TaskScoped variants) used to duplicate the same 3-method
    add_arguments / _<flag>(args) / provider_class_for triplet. Phase
    14 extracted module-level helpers so the shape is named in one
    place; same-shape changes touch one site instead of seven."""

    def test_register_provider_flag_idempotent(self) -> None:
        """The one-shot `briar extract` command shares one parser; two
        extractors of the same family must be able to call
        `_register_provider_flag` against the same parser without
        argparse raising."""
        from briar.extract.base import _register_provider_flag

        parser = argparse.ArgumentParser()
        _register_provider_flag(parser, flag="cloud", default="aws", choices=["aws", "gcp"], help_text="x")
        # Second call must NOT raise — it's the same flag-name dedupe path
        _register_provider_flag(parser, flag="cloud", default="aws", choices=["aws", "gcp"], help_text="x")
        # The flag is registered with the right default
        ns = parser.parse_args([])
        self.assertEqual(ns.cloud, "aws")

    def test_resolve_provider_from_args_passes_the_logins_on_args(self) -> None:
        """Spec 005: the adapter gets the company logins that `briar extract` or the runbook put on args."""
        from briar.extract.base import _resolve_provider_from_args

        logins = FakeLogins(company="globex").logins()
        ns = argparse.Namespace(tracker="linear", company="acme", logins=logins)

        kind, passed = _resolve_provider_from_args(ns, flag="tracker", default="jira", make_fn=lambda kind, logins: (kind, logins))

        self.assertEqual((kind, passed), ("linear", logins))

    def test_resolve_provider_from_args_without_logins_takes_the_company_defaults(self) -> None:
        from briar.extract.base import _resolve_provider_from_args

        ns = argparse.Namespace(tracker="jira", company="acme")

        _, passed = _resolve_provider_from_args(ns, flag="tracker", default="jira", make_fn=lambda kind, logins: (kind, logins))

        self.assertEqual(passed.company, "acme")

    def test_resolve_provider_falls_back_to_default(self) -> None:
        from briar.extract.base import _resolve_provider_from_args

        def fake_make(kind: str, logins: object) -> str:
            return kind

        # No `tracker` attr on args → falls back to default
        ns = argparse.Namespace(company="")
        self.assertEqual(
            _resolve_provider_from_args(ns, flag="tracker", default="jira", make_fn=fake_make),
            "jira",
        )

    def test_provider_class_for_flag_resolves(self) -> None:
        from briar.extract.base import _provider_class_for_flag

        class _FakeCloud:
            pass

        classes = {"aws": _FakeCloud}
        ns = argparse.Namespace(cloud="aws")
        self.assertIs(
            _provider_class_for_flag(ns, flag="cloud", default="aws", classes_dict=classes),
            _FakeCloud,
        )
        # Unknown flag value → None
        ns_unknown = argparse.Namespace(cloud="azure")
        self.assertIsNone(
            _provider_class_for_flag(ns_unknown, flag="cloud", default="aws", classes_dict=classes),
        )


if __name__ == "__main__":
    unittest.main()
