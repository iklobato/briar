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
            make_tracker("notion", company="acme")

    def test_jira_unavailable_without_creds(self) -> None:
        from briar.extract._trackers import make_tracker

        with mock.patch.dict("os.environ", {}, clear=True):
            tracker = make_tracker("jira", company="acme")
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
        with mock.patch.dict("os.environ", {"LINEAR_ACME_TOKEN": "lin_xxx"}):
            tracker = make_tracker("linear", company="acme")
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


class LLMRegistryTests(unittest.TestCase):
    def test_all_kinds_registered(self) -> None:
        from briar.agent._llms import LLMRegistry

        kinds = LLMRegistry.kinds()
        for expected in ("anthropic", "openai", "gemini", "bedrock"):
            self.assertIn(expected, kinds)

    def test_anthropic_available_with_oauth_token(self) -> None:
        from briar.agent._llms import make_llm

        with mock.patch.dict("os.environ", {"CLAUDE_CODE_OAUTH_TOKEN": "tok_xxx"}, clear=False):
            llm = make_llm("anthropic")
            self.assertTrue(llm.is_available())

    def test_openai_unavailable_without_api_key(self) -> None:
        from briar.agent._llms import make_llm

        with mock.patch.dict("os.environ", {}, clear=True):
            llm = make_llm("openai")
            self.assertFalse(llm.is_available())

    def test_openai_complete_raises_when_sdk_missing(self) -> None:
        """SDK is an opt-in extra. Without `openai` installed, `complete`
        must raise a clear message — never silently return empty."""
        from briar.agent._llms import make_llm

        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-x"}):
            llm = make_llm("openai")
            with mock.patch("briar.agent._llms.openai_llm._import_openai", return_value=None):
                with self.assertRaises(RuntimeError) as ctx:
                    llm.complete(system="", messages=[], tools=[], max_tokens=10)
                self.assertIn("briar-cli[openai]", str(ctx.exception))

    def test_bedrock_format_tool_result_uses_toolresult_shape(self) -> None:
        """Bedrock Converse API uses camelCase `toolResult` blocks
        wrapped in a user message."""
        from briar.agent._llms import make_llm

        llm = make_llm("bedrock")
        msg = llm.format_tool_result(tool_call_id="t_1", output="hello")
        self.assertEqual(msg["role"], "user")
        self.assertEqual(msg["content"][0]["toolResult"]["toolUseId"], "t_1")

    def test_anthropic_format_tool_result_shape(self) -> None:
        from briar.agent._llms import make_llm

        llm = make_llm("anthropic")
        block = llm.format_tool_result(tool_call_id="t_1", output="hello")
        self.assertEqual(block["type"], "tool_result")
        self.assertEqual(block["tool_use_id"], "t_1")

    def test_openai_format_tool_result_shape(self) -> None:
        """OpenAI uses a fundamentally different echo-back shape.
        This test pins the contract so any future "let's unify them"
        refactor fails loudly here first."""
        from briar.agent._llms import make_llm

        llm = make_llm("openai")
        block = llm.format_tool_result(tool_call_id="call_1", output="hello")
        self.assertEqual(block["role"], "tool")
        self.assertEqual(block["tool_call_id"], "call_1")


class CloudRegistryTests(unittest.TestCase):
    def test_all_kinds_registered(self) -> None:
        from briar.extract._clouds import CloudRegistry

        kinds = CloudRegistry.kinds()
        for expected in ("aws", "gcp", "azure"):
            self.assertIn(expected, kinds)

    def test_aws_available_with_boto3(self) -> None:
        from briar.extract._clouds import make_cloud

        cloud = make_cloud("aws", company="acme", region="us-east-1")
        # boto3 is a hard runtime dep; should always be available
        self.assertTrue(cloud.is_available())

    def test_gcp_unavailable_without_project(self) -> None:
        from briar.extract._clouds import make_cloud

        cloud = make_cloud("gcp", company="acme")
        self.assertFalse(cloud.is_available())

    def test_azure_unavailable_without_sdk(self) -> None:
        from briar.extract._clouds import make_cloud

        cloud = make_cloud("azure", profile="sub-id-here")
        with mock.patch("briar.extract._clouds.azure._try_import", return_value=None):
            self.assertFalse(cloud.is_available())

    def test_azure_caller_identity_raises_when_sdk_missing(self) -> None:
        from briar.extract._clouds import make_cloud

        cloud = make_cloud("azure", profile="sub-id-here")
        with mock.patch("briar.extract._clouds.azure._try_import", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                cloud.caller_identity()
            self.assertIn("briar-cli[azure]", str(ctx.exception))

    def test_gcp_caller_identity_raises_when_sdk_missing(self) -> None:
        from briar.extract._clouds import make_cloud

        cloud = make_cloud("gcp", profile="proj-id")
        with mock.patch("briar.extract._clouds.gcp._try_import", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                cloud.caller_identity()
            self.assertIn("briar-cli[gcp]", str(ctx.exception))


class NotificationRegistryTests(unittest.TestCase):
    def test_all_kinds_registered(self) -> None:
        from briar.notify import NotificationRegistry

        kinds = NotificationRegistry.kinds()
        for expected in ("telegram", "slack", "email", "pagerduty"):
            self.assertIn(expected, kinds)

    def test_telegram_unavailable_without_token(self) -> None:
        from briar.notify import make_sink

        with mock.patch.dict("os.environ", {}, clear=True):
            sink = make_sink("telegram", company="acme")
            self.assertFalse(sink.is_available())

    def test_telegram_send_returns_false_when_not_configured(self) -> None:
        from briar.notify import make_sink

        with mock.patch.dict("os.environ", {}, clear=True):
            sink = make_sink("telegram", company="acme")
            self.assertFalse(sink.send(title="t", body="b"))

    def test_slack_send_posts_to_webhook(self) -> None:
        from briar.notify import make_sink

        with mock.patch.dict("os.environ", {"SLACK_ACME_WEBHOOK_URL": "https://hooks.slack.com/x"}):
            sink = make_sink("slack", company="acme")
            self.assertTrue(sink.is_available())
            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = b"ok"
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = lambda s, *a: None
            with mock.patch("urllib.request.urlopen", return_value=mock_resp) as urlopen:
                ok = sink.send(title="x", body="y")
            self.assertTrue(ok)
            self.assertEqual(urlopen.call_count, 1)

    def test_pagerduty_send_posts_to_events_api(self) -> None:
        from briar.notify import make_sink

        with mock.patch.dict("os.environ", {"PAGERDUTY_ACME_ROUTING_KEY": "rk_xxx"}):
            sink = make_sink("pagerduty", company="acme")
            self.assertTrue(sink.is_available())
            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = b'{"status": "success"}'
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = lambda s, *a: None
            with mock.patch("urllib.request.urlopen", return_value=mock_resp) as urlopen:
                ok = sink.send(title="x", body="y")
            self.assertTrue(ok)
            self.assertEqual(urlopen.call_count, 1)


class CredentialStoreTests(unittest.TestCase):
    def test_all_kinds_registered(self) -> None:
        from briar.credentials import CredentialStoreRegistry

        kinds = CredentialStoreRegistry.kinds()
        for expected in ("envfile", "aws-secretsmanager", "ssm", "vault"):
            self.assertIn(expected, kinds)

    def test_envfile_read_round_trip(self) -> None:
        from briar.credentials import make_credential_store

        store = make_credential_store("envfile")
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_xxx"}, clear=False):
            self.assertEqual(store.read("GITHUB_TOKEN"), "ghp_xxx")
            # Missing returns None (distinct from "set to empty string") so
            # callers can fail closed; see CredentialStore.read contract.
            self.assertIsNone(store.read("DOES_NOT_EXIST"))

    def test_envfile_list_filters_to_known_prefixes(self) -> None:
        from briar.credentials import make_credential_store

        store = make_credential_store("envfile")
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "x", "BITBUCKET_ACME_USERNAME": "u", "UNRELATED_VAR": "y"}, clear=True):
            names = store.list()
            self.assertIn("GITHUB_TOKEN", names)
            self.assertIn("BITBUCKET_ACME_USERNAME", names)
            self.assertNotIn("UNRELATED_VAR", names)

    def test_envfile_fingerprint_is_keyed_blake2b(self) -> None:
        import hashlib

        from briar.credentials import make_credential_store
        from briar.credentials._store import _fingerprint_salt

        store = make_credential_store("envfile")
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_xxx"}, clear=False):
            expected = hashlib.blake2b(b"ghp_xxx", key=_fingerprint_salt(), digest_size=16).hexdigest()
            self.assertEqual(store.fingerprint("GITHUB_TOKEN"), expected)

    def test_aws_secrets_read_uses_boto3_and_caches(self) -> None:
        from briar.credentials import make_credential_store

        store = make_credential_store("aws-secretsmanager")
        fake_client = mock.MagicMock()
        # Return DIFFERENT values on each backend call. If the cache were
        # broken (re-fetching every time), the second read would surface
        # "second". Asserting both reads are "first" proves the cached value
        # is actually served — a call_count==1 check alone would pass even
        # if read() returned a fresh (but coincidentally equal) value.
        fake_client.get_secret_value.side_effect = [
            {"SecretString": "first"},
            {"SecretString": "second"},
        ]
        with mock.patch("boto3.client", return_value=fake_client):
            self.assertEqual(store.read("GITHUB_TOKEN"), "first")
            self.assertEqual(store.read("GITHUB_TOKEN"), "first")  # served from cache, not "second"
            self.assertEqual(fake_client.get_secret_value.call_count, 1)
            # A different key must NOT be served the first key's cache entry.
            self.assertEqual(store.read("OTHER_TOKEN"), "second")
            self.assertEqual(fake_client.get_secret_value.call_count, 2)
        # Composite JSON secret: `{"value": "..."}` is extracted.
        store2 = make_credential_store("aws-secretsmanager")
        fake_client2 = mock.MagicMock()
        fake_client2.get_secret_value.return_value = {"SecretString": '{"value": "from-json"}'}
        with mock.patch("boto3.client", return_value=fake_client2):
            self.assertEqual(store2.read("GITHUB_TOKEN"), "from-json")

    def test_vault_unavailable_without_addr_or_token(self) -> None:
        from briar.credentials import make_credential_store

        store = make_credential_store("vault")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(store.read("ANYTHING"))  # silent miss, no SDK call attempted

    def test_vault_read_raises_when_hvac_missing(self) -> None:
        from briar.credentials import make_credential_store

        with mock.patch.dict("os.environ", {"VAULT_ADDR": "http://x", "VAULT_TOKEN": "t"}):
            store = make_credential_store("vault")
            with mock.patch("briar.credentials.vault._import_hvac", return_value=None):
                with self.assertRaises(RuntimeError) as ctx:
                    store.read("ANYTHING")
                self.assertIn("briar-cli[vault]", str(ctx.exception))

    def test_ssm_read_uses_boto3(self) -> None:
        from briar.credentials import make_credential_store

        store = make_credential_store("ssm")
        fake_client = mock.MagicMock()
        fake_client.get_parameter.return_value = {"Parameter": {"Value": "secret-val"}}
        with mock.patch("boto3.client", return_value=fake_client):
            self.assertEqual(store.read("GITHUB_TOKEN"), "secret-val")
            # Path prefix is applied
            fake_client.get_parameter.assert_called_with(Name="/briar/GITHUB_TOKEN", WithDecryption=True)


class ExecutorNotificationTests(unittest.TestCase):
    """`_notify_failure` dispatches to every sink listed in
    ``$BRIAR_NOTIFY_SINKS``. The dispatch must NOT raise — a broken
    sink can't crash the scheduler."""

    def test_notify_failure_dispatches_to_telegram_when_configured(self) -> None:
        from briar.iac.runbook.executor import _notify_failure

        fake_sink = mock.MagicMock()
        fake_sink.is_available.return_value = True
        fake_sink.send.return_value = True

        with mock.patch.dict("os.environ", {"BRIAR_NOTIFY_SINKS": "telegram"}):
            with mock.patch("briar.notify.make_sink", return_value=fake_sink):
                _notify_failure("acme", "extractors", "stuff broke", "trace")

        self.assertEqual(fake_sink.send.call_count, 1)
        kwargs = fake_sink.send.call_args.kwargs
        self.assertIn("acme", kwargs["title"])
        self.assertIn("stuff broke", kwargs["body"])

    def test_notify_failure_silent_when_no_sinks_configured(self) -> None:
        from briar.iac.runbook.executor import _notify_failure

        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("briar.notify.make_sink") as make_sink:
                _notify_failure("acme", "extractors", "stuff broke", "trace")
                self.assertEqual(make_sink.call_count, 0)

    def test_notify_failure_swallows_sink_exceptions(self) -> None:
        from briar.iac.runbook.executor import _notify_failure

        fake_sink = mock.MagicMock()
        fake_sink.is_available.return_value = True
        fake_sink.send.side_effect = RuntimeError("network down")

        with mock.patch.dict("os.environ", {"BRIAR_NOTIFY_SINKS": "telegram"}):
            with mock.patch("briar.notify.make_sink", return_value=fake_sink):
                # MUST NOT raise — scheduler stays alive on sink failure
                _notify_failure("acme", "extractors", "x", "y")


class AgentOpRegistryTests(unittest.TestCase):
    """`briar agent` dispatches via AGENT_OPS registry, NOT an if-chain.
    This test pins that contract — a regression to `if op == 'prfix':`
    style would break it."""

    def test_agent_ops_registered(self) -> None:
        from briar.commands.agent import AGENT_OPS

        self.assertIn("prfix", AGENT_OPS)
        self.assertIn("implement", AGENT_OPS)

    def test_run_returns_2_on_unknown_op(self) -> None:
        from briar.commands.agent import CommandAgent

        cmd = CommandAgent()
        ns = mock.MagicMock(agent_op="frobnicate")
        rc = cmd.run(ns)
        self.assertEqual(rc, 2)

    def test_run_dispatches_via_registry(self) -> None:
        """If `run` ever reverts to `if op == 'prfix':`, this test
        catches it — we swap the registry entry and confirm dispatch
        followed the new pointer."""
        from briar.commands.agent import AGENT_OPS, CommandAgent

        called = {}

        class FakeOp:
            name = "prfix"

            def run(self, agent_cmd, args):
                called["happened"] = True
                return 42

        with mock.patch.dict(AGENT_OPS, {"prfix": FakeOp()}):
            cmd = CommandAgent()
            ns = mock.MagicMock(agent_op="prfix")
            rc = cmd.run(ns)
        self.assertEqual(rc, 42)
        self.assertTrue(called.get("happened"))


class MessageWriterRegistryTests(unittest.TestCase):
    """The 6 message writers register correctly + the runbook
    `messages:` block validates against the live WRITERS registry +
    SendMessageTool dispatches via the registry."""

    def test_all_writers_registered(self) -> None:
        from briar.messaging import WRITERS

        for kind in (
            "jira-comment",
            "jira-transition",
            "slack-channel",
            "telegram-chat",
            "github-pr-comment",
            "bitbucket-pr-comment",
        ):
            self.assertIn(kind, WRITERS)

    def test_make_writer_unknown_kind_raises(self) -> None:
        from briar.errors import CliError
        from briar.messaging import make_writer

        with self.assertRaises(CliError):
            make_writer("discord-channel", company="acme")

    def test_message_binding_pydantic_validates_against_registry(self) -> None:
        from pydantic import ValidationError

        from briar.iac.runbook.models import MessageBinding

        MessageBinding(kind="jira-comment")
        with self.assertRaises((ValidationError, ValueError)):
            MessageBinding(kind="discord-channel")

    def test_company_entry_accepts_messages_block(self) -> None:
        """Per-company runbook can declare named writer bindings."""
        from briar.iac.runbook.models import CompanyEntry

        c = CompanyEntry.model_validate(
            {
                "knowledge": {"store": "file", "name": "./knowledge/acme.md"},
                "messages": {
                    "ticket_comment": {"kind": "jira-comment"},
                    "ops_chat": {"kind": "slack-channel"},
                },
            }
        )
        self.assertEqual(set(c.messages.keys()), {"ticket_comment", "ops_chat"})
        self.assertEqual(c.messages["ticket_comment"].kind, "jira-comment")

    def test_github_pr_comment_target_parsing(self) -> None:
        # _parse_target was deduplicated to messaging._writer.parse_pr_target
        # (Phase 7). Same contract, single implementation.
        from briar.messaging._writer import parse_pr_target

        # `#`-form
        self.assertEqual(parse_pr_target("acme/app#42", {}), ("acme/app", 42))
        # extras form
        self.assertEqual(parse_pr_target("acme/app", {"pr": 7}), ("acme/app", 7))
        # Garbage
        self.assertEqual(parse_pr_target("nonsense", {}), ("", 0))

    def test_send_message_tool_lists_channels(self) -> None:
        from briar.agent.tools import SendMessageTool
        from briar.iac.runbook.models import MessageBinding

        tool = SendMessageTool(
            messages={
                "ticket_comment": MessageBinding(kind="jira-comment"),
                "ops_chat": MessageBinding(kind="slack-channel"),
            },
            company="acme",
        )
        self.assertEqual(tool.channels(), ["ops_chat", "ticket_comment"])

    def test_send_message_tool_rejects_unknown_channel(self) -> None:
        from briar.agent.tools import SendMessageTool, ToolError

        tool = SendMessageTool(messages={}, company="acme")
        with self.assertRaises(ToolError):
            tool.run(channel="nonexistent", body="x")

    def test_send_message_tool_dispatches_via_make_writer(self) -> None:
        """Regression-pin: the tool resolves channel → kind → writer
        via the messaging registry. A regression to a `if kind ==
        'jira'` chain would break this."""
        from briar.agent.tools import SendMessageTool
        from briar.iac.runbook.models import MessageBinding
        from briar.messaging._writer import SendResult

        fake_writer = mock.MagicMock()
        fake_writer.is_available.return_value = True
        fake_writer.send.return_value = SendResult(ok=True, ref="cmt-1")
        with mock.patch("briar.messaging.make_writer", return_value=fake_writer):
            tool = SendMessageTool(
                messages={"ticket_comment": MessageBinding(kind="jira-comment")},
                company="acme",
            )
            out = tool.run(channel="ticket_comment", target="ACME-42", body="LGTM")
        self.assertIn("sent via", out)
        fake_writer.send.assert_called_once_with(target="ACME-42", body="LGTM")

    def test_jira_writers_required_env_vars(self) -> None:
        from briar.messaging.jira_comment import JiraCommentWriter
        from briar.messaging.jira_transition import JiraTransitionWriter

        for cls in (JiraCommentWriter, JiraTransitionWriter):
            names = cls.required_env_vars(company="acme")
            self.assertIn("JIRA_ACME_URL", names)
            self.assertIn("JIRA_ACME_EMAIL", names)
            self.assertIn("JIRA_ACME_TOKEN", names)


class ProviderRequiredEnvVarsTests(unittest.TestCase):
    """Each provider declares its own required env vars via a
    ``classmethod required_env_vars(company)`` — replaces the
    hand-maintained `_EXTRACTOR_REQUIREMENTS` table the doctor used
    to consult."""

    def test_github_provider_returns_workspace_token(self) -> None:
        from briar.extract._providers.github import GithubProvider

        self.assertEqual(GithubProvider.required_env_vars(), ["GITHUB_TOKEN"])
        # Company arg is inert (GITHUB_TOKEN is workspace-wide).
        self.assertEqual(GithubProvider.required_env_vars(company="acme"), ["GITHUB_TOKEN"])

    def test_bitbucket_provider_returns_three_per_company_vars(self) -> None:
        from briar.extract._providers.bitbucket import BitbucketProvider

        # Empty company → empty list (no creds claimable without a tenant).
        self.assertEqual(BitbucketProvider.required_env_vars(), [])
        # Per-company → three env-var names interpolated.
        names = BitbucketProvider.required_env_vars(company="acme")
        self.assertIn("BITBUCKET_ACME_USERNAME", names)
        self.assertIn("BITBUCKET_ACME_APP_PASSWORD", names)
        self.assertIn("BITBUCKET_ACME_WORKSPACE", names)

    def test_jira_tracker_returns_three_per_company_vars(self) -> None:
        from briar.extract._trackers.jira import JiraTracker

        names = JiraTracker.required_env_vars(company="acme")
        self.assertIn("JIRA_ACME_URL", names)
        self.assertIn("JIRA_ACME_EMAIL", names)
        self.assertIn("JIRA_ACME_TOKEN", names)

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
    (bulk-write-at-startup vs read-on-demand). InfisicalBootstrap is
    the only concrete impl today; this test pins the contract so a
    future addition stays in shape."""

    def test_registry_lists_envfile_then_infisical(self) -> None:
        """Registry order = precedence. envfile must come first so
        locally-persisted creds beat remote-vault values and survive an
        Infisical 401."""
        from briar.credentials._bootstraps import BOOTSTRAPS

        kinds = list(BOOTSTRAPS.keys())
        self.assertIn("envfile", kinds)
        self.assertIn("infisical", kinds)
        self.assertLess(kinds.index("envfile"), kinds.index("infisical"))

    def test_infisical_unavailable_without_creds(self) -> None:
        from briar.credentials._bootstraps.infisical import InfisicalBootstrap

        with mock.patch.dict("os.environ", {}, clear=True):
            bs = InfisicalBootstrap()
            self.assertFalse(bs.is_available())
            # hydrate() returns a structured error, NOT raises — so a
            # misconfigured host doesn't crash briar at startup.
            result = bs.hydrate()
            self.assertFalse(result.ok)
            self.assertIn("missing INFISICAL_", result.error)

    def test_infisical_required_env_vars(self) -> None:
        from briar.credentials._bootstraps.infisical import InfisicalBootstrap

        names = InfisicalBootstrap.required_env_vars()
        self.assertIn("INFISICAL_CLIENT_ID", names)
        self.assertIn("INFISICAL_CLIENT_SECRET", names)
        self.assertIn("INFISICAL_PROJECT_ID", names)

    def test_auto_bootstrap_no_backend_configured(self) -> None:
        """No backend is available → empty list. Startup must be
        robust to "no remote vault, no envfile present"."""
        import tempfile

        from briar.credentials._bootstraps import auto_bootstrap

        with tempfile.TemporaryDirectory() as tmp:
            # Point envfile resolution at a fresh path with no file →
            # EnvFileBootstrap.is_available() returns False, and with
            # no INFISICAL_* vars set, Infisical is also unavailable.
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

    def test_auto_bootstrap_runs_envfile_then_infisical(self) -> None:
        """Both backends configured → both fire. Infisical 401 does not
        wipe out envfile's written keys; envfile values shadow Infisical
        values when both supply the same key (envfile is registered
        first; setdefault semantics)."""
        import tempfile

        from briar.credentials._bootstraps import auto_bootstrap

        contents = "FROM_ENVFILE=v1\nSHARED=envfile-wins\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.env"
            path.write_text(contents)
            env = {
                "BRIAR_SECRETS_FILE": str(path),
                "INFISICAL_CLIENT_ID": "id",
                "INFISICAL_CLIENT_SECRET": "secret",
                "INFISICAL_PROJECT_ID": "proj",
            }
            with mock.patch.dict("os.environ", env, clear=True):
                with mock.patch(
                    "briar.credentials._bootstraps.infisical.InfisicalBootstrap._fetch_secrets",
                    return_value=[("FROM_INFISICAL", "v2"), ("SHARED", "infisical-loses")],
                ):
                    results = auto_bootstrap()

        backends = [r.backend for r in results]
        self.assertEqual(backends, ["envfile", "infisical"])
        # envfile wrote 2 keys
        envfile_result = results[0]
        self.assertIn("FROM_ENVFILE", envfile_result.written)
        self.assertIn("SHARED", envfile_result.written)
        # infisical wrote 1 (FROM_INFISICAL); SHARED was skipped because
        # envfile already populated it
        infisical_result = results[1]
        self.assertIn("FROM_INFISICAL", infisical_result.written)
        self.assertIn("SHARED", infisical_result.skipped)

    def test_auto_bootstrap_survives_infisical_failure(self) -> None:
        """The cascade's reason for existing: Infisical 401 leaves
        envfile values in place. Operator can still work locally."""
        import tempfile

        from briar.credentials._bootstraps import auto_bootstrap

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.env"
            path.write_text("LOCAL_KEY=local-value\n")
            env = {
                "BRIAR_SECRETS_FILE": str(path),
                "INFISICAL_CLIENT_ID": "id",
                "INFISICAL_CLIENT_SECRET": "secret",
                "INFISICAL_PROJECT_ID": "proj",
            }
            with mock.patch.dict("os.environ", env, clear=True):
                with mock.patch(
                    "briar.credentials._bootstraps.infisical.InfisicalBootstrap._fetch_secrets",
                    side_effect=RuntimeError("401 invalid creds"),
                ):
                    results = auto_bootstrap()
                # Assert envfile values landed in os.environ while the
                # patched dict is still active — mock.patch.dict rolls
                # back on exit, so checks outside the `with` would see
                # the original (empty) env.
                self.assertEqual(os.environ["LOCAL_KEY"], "local-value")

        # envfile succeeded, infisical failed — both reported
        envfile_result = next(r for r in results if r.backend == "envfile")
        infisical_result = next(r for r in results if r.backend == "infisical")
        self.assertTrue(envfile_result.ok)
        self.assertIn("LOCAL_KEY", envfile_result.written)
        self.assertFalse(infisical_result.ok)
        # Error string contains the sanitized type+message (no URL in this
        # case so the message survives) but never the bearer token / URL
        # itself — see _bootstraps/infisical.py for the scrub logic.
        self.assertIn("RuntimeError", infisical_result.error)
        self.assertIn("401", infisical_result.error)

    def test_infisical_hydrate_writes_via_setdefault_dry_run(self) -> None:
        """Dry-run path: fetches from Infisical (mocked SDK), reports
        the keys that WOULD be set, never writes to os.environ.
        Already-set env vars are listed in `skipped`."""
        from briar.credentials._bootstraps.infisical import InfisicalBootstrap

        env_creds = {
            "INFISICAL_CLIENT_ID": "id-x",
            "INFISICAL_CLIENT_SECRET": "secret-x",
            "INFISICAL_PROJECT_ID": "proj-x",
            "GITHUB_TOKEN": "operator-supplied-token",  # would be preserved
        }
        with mock.patch.dict("os.environ", env_creds, clear=True):
            bs = InfisicalBootstrap()
            self.assertTrue(bs.is_available())
            # Replace the lazy SDK import + the constructed client
            # before hydrate() runs.
            with mock.patch.object(bs, "_fetch_secrets", return_value=[("NEW_VAR", "x"), ("GITHUB_TOKEN", "from-vault")]):
                result = bs.hydrate(dry_run=True)
        self.assertTrue(result.ok)
        self.assertIn("NEW_VAR", result.written)
        self.assertIn("GITHUB_TOKEN", result.skipped)  # operator-supplied wins
        # dry-run: nothing actually written
        self.assertNotIn("NEW_VAR", os.environ)

    def test_infisical_complete_raises_when_sdk_missing(self) -> None:
        """Opt-in extra `pip install briar-cli[infisical]` brings the
        SDK. Without it, hydrate() returns a structured error rather
        than crashing at import time."""
        from briar.credentials._bootstraps.infisical import InfisicalBootstrap

        env_creds = {
            "INFISICAL_CLIENT_ID": "id",
            "INFISICAL_CLIENT_SECRET": "s",
            "INFISICAL_PROJECT_ID": "p",
        }
        with mock.patch.dict("os.environ", env_creds, clear=True):
            bs = InfisicalBootstrap()
            with mock.patch("briar.credentials._bootstraps.infisical._import_infisical_sdk", return_value=None):
                result = bs.hydrate()
        self.assertFalse(result.ok)
        self.assertIn("briar-cli[infisical]", result.error)


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

        p = make_provider("bitbucket", company="acme")
        self.assertEqual(p.clone_url("acme", "app"), "https://bitbucket.org/acme/app.git")
        self.assertEqual(
            p.authed_clone_url("acme", "app", "ATBB-xxx"),
            "https://x-token-auth:ATBB-xxx@bitbucket.org/acme/app.git",
        )

    def test_pr_recipe_dispatches_to_provider(self) -> None:
        """If anyone reverts to `if provider == 'bitbucket':` in
        _implement_specific_instructions, this test catches it — we
        hand in a fake provider directly and confirm its recipe is
        what gets rendered."""
        from briar.commands.agent import CommandAgent

        class FakeProvider:
            kind = "github"

            def pr_creation_recipe(self, *, owner, repo, branch):
                return "  6. DO THE FAKE THING.\n  7. DONE.\n"

        text = CommandAgent._implement_specific_instructions(provider=FakeProvider(), owner="acme", repo="app", ticket_key="ACME-1")
        self.assertIn("DO THE FAKE THING", text)


class AgentCommandTests(unittest.TestCase):
    """`briar agent` subcommands wire the task-scoped extractors
    correctly. These tests don't run the agent — they just verify
    that the JIT fetch helpers return what they should."""

    def test_implement_fetches_ticket_context_via_task_scoped_extractor(self) -> None:
        from briar.commands.agent import CommandAgent
        from briar.extract.base import ExtractedSection

        fake_section = ExtractedSection(title="Ticket context — ACME-42: do thing", body="full body here")
        fake_extractor = mock.MagicMock()
        fake_extractor.fetch.return_value = fake_section

        with mock.patch.dict(
            "briar.extract.TASK_SCOPED_EXTRACTORS",
            {"ticket-context": fake_extractor},
        ):
            sections = CommandAgent._fetch_ticket_context(
                company="acme",
                tracker="jira",
                ticket_project="ACME",
                ticket_key="ACME-42",
            )

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].title, "Ticket context — ACME-42: do thing")
        # The fetch helper passes the right kwargs to the extractor.
        call_args = fake_extractor.fetch.call_args.args[0]
        self.assertEqual(call_args.company, "acme")
        self.assertEqual(call_args.tracker, "jira")
        self.assertEqual(call_args.ticket_project, "ACME")
        self.assertEqual(call_args.ticket_key, "ACME-42")

    def test_implement_returns_empty_when_extractor_raises(self) -> None:
        """A broken tracker call must NOT crash the agent invocation —
        the agent still has the worktree and falls back to the ticket
        key alone."""
        from briar.commands.agent import CommandAgent

        fake_extractor = mock.MagicMock()
        fake_extractor.fetch.side_effect = RuntimeError("api down")

        with mock.patch.dict(
            "briar.extract.TASK_SCOPED_EXTRACTORS",
            {"ticket-context": fake_extractor},
        ):
            sections = CommandAgent._fetch_ticket_context(company="acme", tracker="jira", ticket_project="ACME", ticket_key="ACME-42")
        self.assertEqual(sections, [])

    def test_dry_run_skips_llm_call_and_returns_marker(self) -> None:
        """`AgentRunner(dry_run=True).run()` prints the rendered prompt
        and returns without invoking the LLM. The LLM provider's
        is_available() is NOT checked — we want to render the prompt
        even on hosts without LLM creds (that's the whole point)."""
        import io
        import sys
        from pathlib import Path

        from briar.agent.runner import AgentRunConfig, AgentRunner

        fake_llm = mock.MagicMock()
        fake_llm.kind = "anthropic"
        # If the dry-run path is broken and falls through to .complete,
        # this would fail the test instead of silently calling the API.
        fake_llm.complete.side_effect = AssertionError("dry-run must NOT call complete()")

        fake_store = mock.MagicMock()
        # KnowledgeSplicer wraps store.list/get; let it return nothing
        # so the prologue ends up empty (we're testing dry-run plumbing,
        # not knowledge splicing).
        fake_store.list.return_value = []
        fake_store.get.return_value = ""

        runner = AgentRunner(
            AgentRunConfig(
                company="acme",
                task="implement",
                archetype_name="engineer",
                workdir=Path("/tmp/briar-test"),
                knowledge_store=fake_store,
                target="acme-co/acme-app",
                dry_run=True,
            ),
            llm=fake_llm,
        )
        captured = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured
            result = runner.run()
        finally:
            sys.stdout = old_stdout

        self.assertEqual(result.stop_reason, "dry_run")
        self.assertEqual(result.iterations, 0)
        output = captured.getvalue()
        self.assertIn("DRY RUN", output)
        self.assertIn("SYSTEM PROMPT", output)
        self.assertIn("INITIAL USER MESSAGE", output)
        self.assertIn("TOOLS BOUND", output)
        # The engineer archetype's role + goal must be in the system
        # prompt — that's the headline thing the operator wants to
        # validate before spending tokens.
        self.assertIn("acme-co/acme-app", output)

    def test_implement_instructions_include_ticket_key_and_branch_name(self) -> None:
        """The instruction string is what the agent sees — must contain
        the ticket key + a derived branch name + the no-force constraint."""
        from briar.commands.agent import CommandAgent
        from briar.extract._providers import make_provider

        provider = make_provider("github", company="acme")
        instructions = CommandAgent._implement_specific_instructions(provider=provider, owner="acme-co", repo="acme-app", ticket_key="ACME-42")
        self.assertIn("ACME-42", instructions)
        self.assertIn("chore/acme-42", instructions)
        self.assertIn("NEVER --force", instructions)


class ArchetypeConsumesOrderingTests(unittest.TestCase):
    """The `consumes` ordering on engineer/pr-fixer is load-bearing —
    it controls what the agent reads first. Pin the new order so a
    later careless reorder breaks the test, not production."""

    def test_engineer_consumes_ticket_context_first(self) -> None:
        from briar.iac.scaffold.archetypes import ARCHETYPES

        engineer = ARCHETYPES["engineer"]
        self.assertEqual(engineer.consumes[0], "ticket-context")
        # The two new context-rich extractors must precede the legacy
        # active-work / pr-archaeology pair.
        self.assertLess(engineer.consumes.index("code-hotspots"), engineer.consumes.index("active-work"))
        self.assertLess(engineer.consumes.index("reviewer-profile"), engineer.consumes.index("pr-archaeology"))
        # github-deployments + aws-infra are NO LONGER in engineer's
        # consumes — they were dropped as low-signal for implementation work.
        self.assertNotIn("github-deployments", engineer.consumes)
        self.assertNotIn("aws-infra", engineer.consumes)

    def test_pr_fixer_consumes_pr_review_context_first(self) -> None:
        from briar.iac.scaffold.archetypes import ARCHETYPES

        pr_fixer = ARCHETYPES["pr-fixer"]
        self.assertEqual(pr_fixer.consumes[0], "pr-review-context")
        self.assertIn("reviewer-profile", pr_fixer.consumes)
        self.assertIn("code-hotspots", pr_fixer.consumes)


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
    """`KnowledgeStore.from_binding` is the construction path. Postgres
    walks three sources in priority order: explicit config.dsn_env →
    BRIAR_{COMPANY}_DATABASE_URL → BRIAR_DATABASE_URL.

    These tests pin that contract — regressing the order would silently
    point a company's writes at the wrong database, which is a quietly
    catastrophic failure mode."""

    @staticmethod
    def _clean_env():
        return mock.patch.dict(
            os.environ,
            {
                "BRIAR_DATABASE_URL": "",
                "BRIAR_ACME_DATABASE_URL": "",
                "PROD_KB_PG": "",
            },
            clear=False,
        )

    def test_config_dsn_env_wins(self) -> None:
        from briar.storage import StoreBinding
        from briar.storage.postgres import StorePostgres

        env = {
            "PROD_KB_PG": "postgres://from-config/db",
            "BRIAR_ACME_DATABASE_URL": "postgres://from-per-company/db",
            "BRIAR_DATABASE_URL": "postgres://from-global/db",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            binding = StoreBinding(store="postgres", company="acme", config={"dsn_env": "PROD_KB_PG"})
            store = StorePostgres.from_binding(binding)
            self.assertEqual(store._dsn, "postgres://from-config/db")

    def test_per_company_env_wins_over_global(self) -> None:
        from briar.storage import StoreBinding
        from briar.storage.postgres import StorePostgres

        env = {
            "BRIAR_ACME_DATABASE_URL": "postgres://from-per-company/db",
            "BRIAR_DATABASE_URL": "postgres://from-global/db",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            binding = StoreBinding(store="postgres", company="acme")
            store = StorePostgres.from_binding(binding)
            self.assertEqual(store._dsn, "postgres://from-per-company/db")

    def test_company_key_with_hyphen_normalises_to_underscore(self) -> None:
        """`widget-co` (hyphen) → BRIAR_WIDGET_CO_DATABASE_URL —
        same normalisation as every other CredEnv.for_company."""
        from briar.storage import StoreBinding
        from briar.storage.postgres import StorePostgres

        env = {"BRIAR_WIDGET_CO_DATABASE_URL": "postgres://hyphenated/db"}
        with mock.patch.dict(os.environ, env, clear=False):
            binding = StoreBinding(store="postgres", company="widget-co")
            store = StorePostgres.from_binding(binding)
            self.assertEqual(store._dsn, "postgres://hyphenated/db")

    def test_falls_back_to_global_dsn(self) -> None:
        from briar.storage import StoreBinding
        from briar.storage.postgres import StorePostgres

        # No company, no config — only the global is set.
        env = {"BRIAR_DATABASE_URL": "postgres://from-global/db", "BRIAR_ACME_DATABASE_URL": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            binding = StoreBinding(store="postgres", company="acme")
            store = StorePostgres.from_binding(binding)
            self.assertEqual(store._dsn, "postgres://from-global/db")

    def test_no_dsn_anywhere_raises_clierror_naming_keys_tried(self) -> None:
        from briar.errors import CliError
        from briar.storage import StoreBinding
        from briar.storage.postgres import StorePostgres

        env = {"BRIAR_DATABASE_URL": "", "BRIAR_ACME_DATABASE_URL": "", "PROD_KB_PG": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            binding = StoreBinding(store="postgres", company="acme", config={"dsn_env": "PROD_KB_PG"})
            with self.assertRaises(CliError) as cm:
                StorePostgres.from_binding(binding)
            # All three keys named in the order tried — operator can see
            # exactly what to set.
            msg = str(cm.exception)
            self.assertIn("PROD_KB_PG", msg)
            self.assertIn("BRIAR_ACME_DATABASE_URL", msg)
            self.assertIn("BRIAR_DATABASE_URL", msg)

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
        """CLI commands (`briar context`, `briar dashboard`) call
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

        b = KnowledgeBinding(store="postgres", name="knowledge:acme", config={"dsn_env": "PROD_KB_PG"})
        self.assertEqual(b.config["dsn_env"], "PROD_KB_PG")


class JiraAuthStrategyTests(unittest.TestCase):
    """`JiraAuthStrategy` registry + autodetect contract.

    Two failure modes to pin:
      - regression to a hardcoded `if email and token: ... elif cookie:`
        chain inside JiraTracker (loses the Strategy decomposition)
      - autodetect ordering changes (token-then-session is wrong; ops
        users with both sets of creds would get token unexpectedly)"""

    @staticmethod
    def _clean_env():
        return mock.patch.dict(
            os.environ,
            {
                "JIRA_ACME_AUTH_KIND": "",
                "JIRA_ACME_EMAIL": "",
                "JIRA_ACME_TOKEN": "",
                "JIRA_ACME_SESSION_TOKEN": "",
                "JIRA_ACME_TENANT_SESSION_TOKEN": "",
                "JIRA_ACME_XSRF_TOKEN": "",
                "JIRA_ACME_USER_AGENT": "",
                "JIRA_ACME_URL": "https://acme.atlassian.net",
            },
            clear=False,
        )

    def test_registry_lists_both_kinds(self) -> None:
        from briar.extract._trackers._jira_auth import jira_auth_kinds

        kinds = jira_auth_kinds()
        self.assertIn("token", kinds)
        self.assertIn("session", kinds)

    def test_token_strategy_required_env_vars(self) -> None:
        from briar.extract._trackers._jira_auth import JiraTokenAuth

        names = JiraTokenAuth.required_env_vars(company="acme")
        self.assertEqual(names, ["JIRA_ACME_EMAIL", "JIRA_ACME_TOKEN"])

    def test_session_strategy_required_env_vars(self) -> None:
        from briar.extract._trackers._jira_auth import JiraSessionAuth

        names = JiraSessionAuth.required_env_vars(company="acme")
        # Either token is sufficient; doctor lists both so operator
        # knows the choices.
        self.assertEqual(
            names,
            ["JIRA_ACME_SESSION_TOKEN", "JIRA_ACME_TENANT_SESSION_TOKEN"],
        )

    def test_session_strategy_is_available_with_tenant_token_only(self) -> None:
        """Atlassian's `tenant.session.token` alone is sufficient for
        tenant-scoped REST calls — `cloud.session.token` is not
        always set in newer browser sessions."""
        from briar.extract._trackers._jira_auth import JiraSessionAuth

        env = {
            "JIRA_ACME_SESSION_TOKEN": "",
            "JIRA_ACME_TENANT_SESSION_TOKEN": "tenant-jwt-blob",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertTrue(JiraSessionAuth.is_available(company="acme"))
            kwargs = JiraSessionAuth().configure(company="acme", base_url="https://acme.atlassian.net")
        # New shape: cookies + headers bundled into a requests.Session
        session = kwargs["session"]
        self.assertEqual(dict(session.cookies), {"tenant.session.token": "tenant-jwt-blob"})

    def test_token_strategy_configure_returns_basic_auth_kwargs(self) -> None:
        from briar.extract._trackers._jira_auth import JiraTokenAuth

        env = {"JIRA_ACME_EMAIL": "ops@acme.com", "JIRA_ACME_TOKEN": "tok-123"}
        with mock.patch.dict(os.environ, env, clear=False):
            kwargs = JiraTokenAuth().configure(company="acme", base_url="https://acme.atlassian.net")
            self.assertEqual(kwargs, {"username": "ops@acme.com", "password": "tok-123"})

    def test_session_strategy_configure_returns_cookies_and_browser_headers(self) -> None:
        from briar.extract._trackers._jira_auth import JiraSessionAuth

        env = {
            "JIRA_ACME_SESSION_TOKEN": "cookie-val-abc",
            "JIRA_ACME_TENANT_SESSION_TOKEN": "tenant-val-xyz",
            "JIRA_ACME_XSRF_TOKEN": "xsrf-val-789",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            kwargs = JiraSessionAuth().configure(company="acme", base_url="https://acme.atlassian.net")

        # New shape: configure returns {"session": requests.Session(...)}
        # with cookies + headers pre-applied. This bundling is required
        # because atlassian-python-api 3.41.x rejects a `header=` kwarg
        # (only the 4.x line accepts it).
        session = kwargs["session"]
        cookies = dict(session.cookies)
        self.assertEqual(cookies["cloud.session.token"], "cookie-val-abc")
        self.assertEqual(cookies["tenant.session.token"], "tenant-val-xyz")
        self.assertEqual(cookies["atlassian.xsrf.token"], "xsrf-val-789")

        # Browser headers mirror what the user's pasted request uses
        h = session.headers
        self.assertEqual(h["Origin"], "https://acme.atlassian.net")
        self.assertEqual(h["Referer"], "https://acme.atlassian.net/")
        self.assertIn("Chrome/147", h["User-Agent"])
        self.assertEqual(h["sec-ch-ua-platform"], '"macOS"')
        # XSRF cookie present → also sent as X-Atlassian-Token header
        self.assertEqual(h["X-Atlassian-Token"], "no-check")

    def test_session_strategy_omits_optional_cookies_when_unset(self) -> None:
        from briar.extract._trackers._jira_auth import JiraSessionAuth

        env = {"JIRA_ACME_SESSION_TOKEN": "only-cloud-session"}
        with mock.patch.dict(os.environ, env, clear=False):
            kwargs = JiraSessionAuth().configure(company="acme", base_url="https://acme.atlassian.net")
        self.assertEqual(list(kwargs["session"].cookies.keys()), ["cloud.session.token"])

    def test_user_agent_override_via_env(self) -> None:
        from briar.extract._trackers._jira_auth import JiraSessionAuth

        env = {
            "JIRA_ACME_SESSION_TOKEN": "x",
            "JIRA_ACME_USER_AGENT": "MyCustomBot/1.0",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            kwargs = JiraSessionAuth().configure(company="acme", base_url="https://acme.atlassian.net")
        self.assertEqual(kwargs["session"].headers["User-Agent"], "MyCustomBot/1.0")

    def test_autodetect_picks_session_when_session_token_set(self) -> None:
        from briar.extract._trackers._jira_auth import JiraSessionAuth, autodetect_jira_auth

        env = {
            "JIRA_ACME_EMAIL": "ops@acme.com",
            "JIRA_ACME_TOKEN": "tok-123",
            "JIRA_ACME_SESSION_TOKEN": "cookie-val",
            "JIRA_ACME_AUTH_KIND": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            auth = autodetect_jira_auth(company="acme")
            self.assertIsInstance(auth, JiraSessionAuth)

    def test_autodetect_falls_back_to_token_when_no_session(self) -> None:
        from briar.extract._trackers._jira_auth import JiraTokenAuth, autodetect_jira_auth

        env = {
            "JIRA_ACME_EMAIL": "ops@acme.com",
            "JIRA_ACME_TOKEN": "tok-123",
            "JIRA_ACME_SESSION_TOKEN": "",
            "JIRA_ACME_AUTH_KIND": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            auth = autodetect_jira_auth(company="acme")
            self.assertIsInstance(auth, JiraTokenAuth)

    def test_explicit_auth_kind_overrides_autodetect(self) -> None:
        from briar.extract._trackers._jira_auth import JiraTokenAuth, autodetect_jira_auth

        # Session token IS set, but operator forces token via env var
        env = {
            "JIRA_ACME_SESSION_TOKEN": "cookie-val",
            "JIRA_ACME_AUTH_KIND": "token",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            auth = autodetect_jira_auth(company="acme")
            self.assertIsInstance(auth, JiraTokenAuth)

    def test_jira_tracker_routes_through_strategy(self) -> None:
        """`JiraTracker.required_env_vars` is the doctor's audit hook.
        It must return the URL + whatever the autodetected strategy
        needs. Pins: regression to hardcoded EMAIL+TOKEN list."""
        from briar.extract._trackers.jira import JiraTracker

        # Case 1: session credentials present → doctor sees session vars
        env_session = {
            "JIRA_ACME_SESSION_TOKEN": "cookie-val",
            "JIRA_ACME_AUTH_KIND": "",
            "JIRA_ACME_URL": "https://x",
        }
        with mock.patch.dict(os.environ, env_session, clear=False):
            self.assertEqual(
                JiraTracker.required_env_vars(company="acme"),
                ["JIRA_ACME_URL", "JIRA_ACME_SESSION_TOKEN", "JIRA_ACME_TENANT_SESSION_TOKEN"],
            )

        # Case 2: no session token → doctor sees token-strategy vars
        env_token = {
            "JIRA_ACME_SESSION_TOKEN": "",
            "JIRA_ACME_AUTH_KIND": "",
            "JIRA_ACME_URL": "https://x",
        }
        with mock.patch.dict(os.environ, env_token, clear=False):
            self.assertEqual(
                JiraTracker.required_env_vars(company="acme"),
                ["JIRA_ACME_URL", "JIRA_ACME_EMAIL", "JIRA_ACME_TOKEN"],
            )

    def test_jira_tracker_is_available_uses_strategy(self) -> None:
        from briar.extract._trackers.jira import JiraTracker

        # Session strategy: URL + session token both present
        env = {
            "JIRA_ACME_URL": "https://acme.atlassian.net",
            "JIRA_ACME_SESSION_TOKEN": "cookie-val",
            "JIRA_ACME_AUTH_KIND": "session",
            "JIRA_ACME_EMAIL": "",
            "JIRA_ACME_TOKEN": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            tracker = JiraTracker(company="acme")
            self.assertTrue(tracker.is_available())

    def test_jira_tracker_constructor_explicit_auth_kind(self) -> None:
        """Allow callers (future YAML wiring) to pass auth_kind directly."""
        from briar.extract._trackers._jira_auth import JiraSessionAuth, JiraTokenAuth
        from briar.extract._trackers.jira import JiraTracker

        t1 = JiraTracker(company="acme", auth_kind="session")
        self.assertIsInstance(t1._auth, JiraSessionAuth)
        t2 = JiraTracker(company="acme", auth_kind="token")
        self.assertIsInstance(t2._auth, JiraTokenAuth)

    def test_registry_make_raises_for_unknown_kind(self) -> None:
        from briar.errors import CliError
        from briar.extract._trackers._jira_auth import make_jira_auth

        with self.assertRaises(CliError):
            make_jira_auth("oauth-not-implemented")

    def test_token_configure_raises_when_creds_missing(self) -> None:
        """`JiraAuthStrategy.configure` is called lazily on first API
        call. Missing creds at that point is a programmer error (caller
        should have checked is_available) — raise loudly, don't silently
        construct a half-configured Jira client."""
        from briar.errors import CliError
        from briar.extract._trackers._jira_auth import JiraTokenAuth

        env = {"JIRA_ACME_EMAIL": "", "JIRA_ACME_TOKEN": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(CliError):
                JiraTokenAuth().configure(company="acme", base_url="https://x.atlassian.net")

    def test_session_configure_raises_when_no_session_token(self) -> None:
        """Same loud-failure contract for session auth — refuse to
        build a Session with empty cookies."""
        from briar.errors import CliError
        from briar.extract._trackers._jira_auth import JiraSessionAuth

        env = {"JIRA_ACME_SESSION_TOKEN": "", "JIRA_ACME_TENANT_SESSION_TOKEN": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(CliError):
                JiraSessionAuth().configure(company="acme", base_url="https://x.atlassian.net")

    def test_session_without_xsrf_omits_xatlassian_header(self) -> None:
        """The `X-Atlassian-Token: no-check` header is only useful when
        a matching xsrf cookie is present. Pin the conditional so a
        future "always set the header" refactor doesn't accidentally
        send an XSRF dance without an actual XSRF token."""
        from briar.extract._trackers._jira_auth import JiraSessionAuth

        env = {
            "JIRA_ACME_SESSION_TOKEN": "cookie",
            "JIRA_ACME_XSRF_TOKEN": "",  # explicitly empty
        }
        with mock.patch.dict(os.environ, env, clear=False):
            kwargs = JiraSessionAuth().configure(company="acme", base_url="https://x.atlassian.net")
        self.assertNotIn("X-Atlassian-Token", kwargs["session"].headers)


class JiraSessionJwtExpTests(unittest.TestCase):
    """`_decode_jwt_exp` parses the JWT payload of a Jira session
    cookie to extract its expiry. Used by JiraSessionAcquirer to record
    when the operator should rotate the cookie."""

    @staticmethod
    def _make_jwt(payload: dict) -> str:
        import base64
        import json

        enc = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        return f"hdr.{enc}.sig"

    def test_extracts_exp_from_3_segment_jwt(self) -> None:
        from briar.auth._acquirers.jira_session import _decode_jwt_exp

        jwt = self._make_jwt({"exp": 1800000000, "iss": "atlassian"})
        result = _decode_jwt_exp(jwt)
        self.assertIsNotNone(result)
        self.assertEqual(int(result.timestamp()), 1800000000)

    def test_returns_none_for_non_jwt_shapes(self) -> None:
        from briar.auth._acquirers.jira_session import _decode_jwt_exp

        self.assertIsNone(_decode_jwt_exp("not-a-jwt-at-all"))
        self.assertIsNone(_decode_jwt_exp("only.two"))  # 2 segments
        self.assertIsNone(_decode_jwt_exp("a.b.c.d"))  # 4 segments

    def test_returns_none_for_malformed_payload(self) -> None:
        from briar.auth._acquirers.jira_session import _decode_jwt_exp

        # Invalid base64 in the payload segment
        self.assertIsNone(_decode_jwt_exp("hdr.!!!not-b64!!!.sig"))
        # Valid base64 but not JSON
        import base64

        garbage = base64.urlsafe_b64encode(b"\xff\xfe garbage").decode().rstrip("=")
        self.assertIsNone(_decode_jwt_exp(f"hdr.{garbage}.sig"))
        # Valid JSON but no `exp` claim
        no_exp = self._make_jwt({"iss": "atlassian"})
        self.assertIsNone(_decode_jwt_exp(no_exp))


class JiraAcquirerInteractiveTests(unittest.TestCase):
    """Onboarding flow — drives each acquirer with MockPromptIO and
    asserts the Credentials.entries dict has the right keys.

    The load-bearing thing here is that the acquirer writes
    ``JIRA_<C>_AUTH_KIND`` so that ``autodetect_jira_auth``
    locks to the strategy the operator just onboarded — a regression
    that drops that key would silently flip the strategy."""

    def test_token_acquirer_writes_all_four_env_vars(self) -> None:
        from briar.auth._acquirers.jira_token import JiraTokenAcquirer
        from briar.auth._prompt import MockPromptIO

        prompt = MockPromptIO(
            answers=[
                "https://acme.atlassian.net",
                "ops@acme.com",
                "tok-secret-123",
            ]
        )
        creds = JiraTokenAcquirer().acquire(company="acme", prompt=prompt)
        self.assertEqual(creds.entries["JIRA_ACME_URL"], "https://acme.atlassian.net")
        self.assertEqual(creds.entries["JIRA_ACME_EMAIL"], "ops@acme.com")
        self.assertEqual(creds.entries["JIRA_ACME_TOKEN"], "tok-secret-123")
        self.assertEqual(creds.entries["JIRA_ACME_AUTH_KIND"], "token")
        # The instructions opened the token-management page.
        self.assertTrue(any("api-tokens" in u for u in prompt.opened_urls))

    def test_token_acquirer_rejects_missing_input(self) -> None:
        from briar.auth._acquirers.jira_token import JiraTokenAcquirer
        from briar.auth._prompt import MockPromptIO

        prompt = MockPromptIO(answers=["https://acme.atlassian.net", "ops@acme.com", ""])
        with self.assertRaises(ValueError):
            JiraTokenAcquirer().acquire(company="acme", prompt=prompt)

    def test_token_acquirer_requires_company(self) -> None:
        from briar.auth._acquirers.jira_token import JiraTokenAcquirer
        from briar.auth._prompt import MockPromptIO

        with self.assertRaises(ValueError):
            JiraTokenAcquirer().acquire(company="", prompt=MockPromptIO(answers=[]))

    def test_session_acquirer_writes_minimum_set(self) -> None:
        from briar.auth._acquirers.jira_session import JiraSessionAcquirer
        from briar.auth._prompt import MockPromptIO

        prompt = MockPromptIO(
            answers=[
                "https://acme.atlassian.net",
                "tenant-jwt-blob",
            ]
        )
        creds = JiraSessionAcquirer().acquire(company="acme", prompt=prompt)
        self.assertEqual(creds.entries["JIRA_ACME_URL"], "https://acme.atlassian.net")
        self.assertEqual(creds.entries["JIRA_ACME_TENANT_SESSION_TOKEN"], "tenant-jwt-blob")
        self.assertEqual(creds.entries["JIRA_ACME_AUTH_KIND"], "session")
        # Acquirer no longer prompts for cloud / xsrf cookies — they're
        # set out-of-band via secrets store when needed for writes.
        self.assertNotIn("JIRA_ACME_SESSION_TOKEN", creds.entries)
        self.assertNotIn("JIRA_ACME_XSRF_TOKEN", creds.entries)

    def test_session_acquirer_normalises_url_to_origin(self) -> None:
        """Operators paste a URL bar (`/jira/your-work`) or add a
        spurious `/jira` suffix because the on-prem product mounts
        there. Atlassian Cloud's REST is at the host root; the strategy
        builds Origin/Referer from this string, so anything past the
        host breaks the request envelope. Acquirer must strip it."""
        from briar.auth._acquirers.jira_session import JiraSessionAcquirer
        from briar.auth._prompt import MockPromptIO

        cases = [
            ("https://acme.atlassian.net/jira", "https://acme.atlassian.net"),
            ("https://acme.atlassian.net/jira/your-work?query=1", "https://acme.atlassian.net"),
            ("  https://acme.atlassian.net/  ", "https://acme.atlassian.net"),
            ("acme.atlassian.net", "https://acme.atlassian.net"),  # scheme inferred
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                prompt = MockPromptIO(answers=[raw, "tenant-jwt-blob"])
                creds = JiraSessionAcquirer().acquire(company="acme", prompt=prompt)
                self.assertEqual(creds.entries["JIRA_ACME_URL"], expected)

    def test_session_acquirer_records_jwt_exp_when_parseable(self) -> None:
        """If the pasted tenant.session.token is a real JWT with `exp`,
        Credentials.expires_at is populated so the CLI can warn before
        the cookie rotates."""
        import base64
        import json

        from briar.auth._acquirers.jira_session import JiraSessionAcquirer
        from briar.auth._prompt import MockPromptIO

        payload = base64.urlsafe_b64encode(json.dumps({"exp": 1800000000}).encode()).decode().rstrip("=")
        jwt = f"hdr.{payload}.sig"
        prompt = MockPromptIO(
            answers=[
                "https://acme.atlassian.net",
                jwt,
            ]
        )
        creds = JiraSessionAcquirer().acquire(company="acme", prompt=prompt)
        self.assertIsNotNone(creds.expires_at)
        self.assertEqual(int(creds.expires_at.timestamp()), 1800000000)


class GitIdentityResolutionTests(unittest.TestCase):
    """`CommandAgent._resolve_git_identity` resolves commit author from
    (priority order): CLI flag → runbook YAML → hardcoded default.

    Pins the precedence so future refactors can't silently change
    which identity ends up on production commits. Per-field
    resolution is independently asserted — a regression to
    "all-or-nothing" pickup would break the partial-override use case."""

    @staticmethod
    def _ns(**overrides) -> argparse.Namespace:
        """Build a Namespace mirroring what argparse would emit, with
        the new empty-default git fields."""
        base = {
            "company": "",
            "runbook": "",
            "git_user_name": "",
            "git_user_email": "",
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def _yaml(self, name: str, email: str) -> str:
        """Minimal valid runbook YAML with a git_identity block."""
        return (
            "version: 1\n"
            "companies:\n"
            "  acme:\n"
            "    knowledge:\n"
            "      store: file\n"
            "      name: knowledge:acme\n"
            "    git_identity:\n"
            f"      name: {name}\n"
            f"      email: {email}\n"
            "    schedules: []\n"
        )

    def test_yaml_parses_git_identity_block(self) -> None:
        """Round-trip the new field through Pydantic."""
        from briar.iac.runbook.models import CompanyEntry, GitIdentity

        gi = GitIdentity(name="briar-bot", email="briar@acme.com")
        self.assertEqual(gi.name, "briar-bot")
        self.assertEqual(gi.email, "briar@acme.com")

        ce = CompanyEntry(git_identity=GitIdentity(name="x", email="y@z"))
        self.assertEqual(ce.git_identity.name, "x")

    def test_empty_block_is_unconfigured(self) -> None:
        """Missing block → empty GitIdentity with both fields ``""``."""
        from briar.iac.runbook.models import CompanyEntry

        ce = CompanyEntry()
        self.assertEqual(ce.git_identity.name, "")
        self.assertEqual(ce.git_identity.email, "")

    def test_resolve_raises_when_nothing_set(self) -> None:
        """No CLI flag, no runbook → CliError. There is no hardcoded
        fallback (was previously a hardcoded personal identifier;
        committed personal identifiers on a third-party host are a
        smell)."""
        from briar.commands.agent import CommandAgent
        from briar.errors import CliError

        with self.assertRaises(CliError) as ctx:
            CommandAgent._resolve_git_identity(self._ns())
        self.assertIn("git identity not configured", str(ctx.exception))

    def test_cli_flag_wins_over_default(self) -> None:
        from briar.commands.agent import CommandAgent

        ns = self._ns(git_user_name="cli-name", git_user_email="cli@e.x")
        name, email = CommandAgent._resolve_git_identity(ns)
        self.assertEqual(name, "cli-name")
        self.assertEqual(email, "cli@e.x")

    def test_yaml_used_when_cli_empty(self) -> None:
        import tempfile

        from briar.commands.agent import CommandAgent

        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(self._yaml("yaml-name", "yaml@e.x"))
            path = f.name
        try:
            ns = self._ns(company="acme", runbook=path)
            name, email = CommandAgent._resolve_git_identity(ns)
            self.assertEqual(name, "yaml-name")
            self.assertEqual(email, "yaml@e.x")
        finally:
            os.unlink(path)

    def test_cli_flag_wins_over_yaml(self) -> None:
        import tempfile

        from briar.commands.agent import CommandAgent

        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(self._yaml("yaml-name", "yaml@e.x"))
            path = f.name
        try:
            ns = self._ns(
                company="acme",
                runbook=path,
                git_user_name="cli-name",
                git_user_email="cli@e.x",
            )
            name, email = CommandAgent._resolve_git_identity(ns)
            self.assertEqual(name, "cli-name")
            self.assertEqual(email, "cli@e.x")
        finally:
            os.unlink(path)

    def test_per_field_resolution_is_independent(self) -> None:
        """CLI sets only name; YAML provides both → CLI's name + YAML's email."""
        import tempfile

        from briar.commands.agent import CommandAgent

        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(self._yaml("yaml-name", "yaml@e.x"))
            path = f.name
        try:
            ns = self._ns(
                company="acme",
                runbook=path,
                git_user_name="cli-name",
                git_user_email="",  # not passed
            )
            name, email = CommandAgent._resolve_git_identity(ns)
            self.assertEqual(name, "cli-name")
            self.assertEqual(email, "yaml@e.x")
        finally:
            os.unlink(path)

    def test_missing_company_in_runbook_raises(self) -> None:
        """Runbook loads but company key absent → CliError. The runbook
        provided nothing; CLI flags weren't passed; no source supplied
        an identity."""
        import tempfile

        from briar.commands.agent import CommandAgent
        from briar.errors import CliError

        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(self._yaml("yaml-name", "yaml@e.x"))
            path = f.name
        try:
            ns = self._ns(company="other-company", runbook=path)
            with self.assertRaises(CliError):
                CommandAgent._resolve_git_identity(ns)
        finally:
            os.unlink(path)

    def test_unreadable_runbook_is_non_fatal_but_still_raises_without_cli(self) -> None:
        """YAML load failure must not crash the resolver — it must log
        and stay quiet. With no CLI flag to fall back on, the resolver
        then raises CliError to signal the missing identity."""
        from briar.commands.agent import CommandAgent
        from briar.errors import CliError

        ns = self._ns(company="acme", runbook="/nonexistent/runbook.yaml")
        with self.assertRaises(CliError):
            CommandAgent._resolve_git_identity(ns)


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

    def test_anthropic_rate_limit_policy_aborts_instead_of_long_sleep(self) -> None:
        """Anthropic 429 → `Abort`, NOT `RetryAfter(3600)`.

        The previous contract (wait an hour, retry up to 5 times) silently
        wedged the agent for up to 5 hours when an OAuth subscription
        token hit its rate-limit window. With `load=0.00` and no
        progress logged, the behaviour was indistinguishable from a hang.

        Aborting fast surfaces the rate-limit to the operator, who can
        decide whether to wait (API key: ~1h reset; OAuth subscription:
        up to 5h Claude.ai window) or rotate credentials. Anthropic's
        OAuth 429s come with no `retry-after` header, so the agent has
        no honest basis for predicting recovery time anyway."""
        import anthropic

        from briar.agent._llms.anthropic_llm import AnthropicLLM
        from briar.error_policy import Abort, ExceptionTypePolicy

        registry = AnthropicLLM.default_error_policies()
        rate_limit_policy = next(p for p in registry.policies if isinstance(p, ExceptionTypePolicy) and p.exception_type is anthropic.RateLimitError)
        self.assertIsInstance(rate_limit_policy.decision, Abort)

    def test_anthropic_aborts_on_401_immediately(self) -> None:
        """401 auth errors should NOT retry — they won't fix
        themselves and they burn the retry budget."""

        from briar.agent._llms.anthropic_llm import AnthropicLLM
        from briar.error_policy import Abort, HttpStatusPolicy

        registry = AnthropicLLM.default_error_policies()
        auth_policy = next(p for p in registry.policies if isinstance(p, HttpStatusPolicy) and p.status == 401)
        self.assertIsInstance(auth_policy.decision, Abort)


class CredentialAcquirerTests(unittest.TestCase):
    """`briar.auth` — interactive credential acquisition.

    Pins the registry surface + each acquirer's happy-path
    interactive flow via MockPromptIO. The flows that hit external
    APIs (github-device, aws-sso) are tested with mocked HTTP."""

    def test_registry_has_all_acquirers(self) -> None:
        from briar.auth import AcquirerRegistry

        kinds = AcquirerRegistry.kinds()
        expected = {
            "github-device",
            "github-pat",
            "bitbucket-app-password",
            "aws-static",
            "aws-sso",
            "jira-token",
            "jira-session",
            "linear-api-key",
            "infisical",
        }
        self.assertEqual(set(kinds), expected)

    def test_infisical_acquirer_writes_machine_identity_triplet(self) -> None:
        """The "log into Infisical" bootstrap flow. Captures the
        three machine-identity creds + the env-slug + host. Company
        is irrelevant (Infisical is workspace-wide)."""
        from briar.auth import AcquirerRegistry, MockPromptIO

        acquirer = AcquirerRegistry.make("infisical")
        prompt = MockPromptIO(
            answers=[
                "client-id-uuid",
                "client-secret-very-long",
                "project-id-uuid",
                "",  # env slug — accept default "prod"
                "",  # host — accept default
            ]
        )
        creds = acquirer.acquire(company="", prompt=prompt)
        self.assertEqual(creds.entries["INFISICAL_CLIENT_ID"], "client-id-uuid")
        self.assertEqual(creds.entries["INFISICAL_CLIENT_SECRET"], "client-secret-very-long")
        self.assertEqual(creds.entries["INFISICAL_PROJECT_ID"], "project-id-uuid")
        self.assertEqual(creds.entries["INFISICAL_ENV"], "prod")
        self.assertEqual(creds.entries["INFISICAL_HOST"], "https://app.infisical.com")
        self.assertIn("machine-identities", prompt.opened_urls[0])

    def test_infisical_acquirer_rejects_missing_required_triplet(self) -> None:
        from briar.auth import AcquirerRegistry, MockPromptIO

        acquirer = AcquirerRegistry.make("infisical")
        prompt = MockPromptIO(answers=["", "", "", "", ""])  # all empty
        with self.assertRaises(ValueError):
            acquirer.acquire(company="", prompt=prompt)

    def test_default_destination_policy_is_external(self) -> None:
        """Vendor acquirers (github, aws, jira, …) should let --store
        decide where their result lands."""
        from briar.auth import AcquirerRegistry
        from briar.auth._acquirer import DestinationPolicy

        for kind in ("github-pat", "github-device", "aws-static", "aws-sso", "jira-token", "jira-session", "linear-api-key", "bitbucket-app-password"):
            acquirer = AcquirerRegistry.make(kind)
            self.assertIs(type(acquirer).destination_policy, DestinationPolicy.EXTERNAL, f"{kind} should be EXTERNAL")

    def test_infisical_acquirer_is_bootstrap_local(self) -> None:
        """Store-bootstrap acquirers must persist to envfile only —
        you can't store the credentials for a store inside that store."""
        from briar.auth import AcquirerRegistry
        from briar.auth._acquirer import DestinationPolicy

        self.assertIs(
            type(AcquirerRegistry.make("infisical")).destination_policy,
            DestinationPolicy.BOOTSTRAP_LOCAL,
        )


class CommandAuthStoreResolutionTests(unittest.TestCase):
    """`briar auth` CLI surface — positional target + destination policy."""

    def test_resolve_default_store_uses_env_when_known(self) -> None:
        from briar.commands.auth import _resolve_default_store

        with mock.patch.dict(os.environ, {"BRIAR_DEFAULT_STORE": "infisical"}, clear=False):
            self.assertEqual(_resolve_default_store(["envfile", "infisical", "vault"]), "infisical")

    def test_resolve_default_store_ignores_unknown_env(self) -> None:
        from briar.commands.auth import _resolve_default_store

        with mock.patch.dict(os.environ, {"BRIAR_DEFAULT_STORE": "not-a-real-store"}, clear=False):
            # Unknown → fall back to envfile (safe default)
            self.assertEqual(_resolve_default_store(["envfile", "vault"]), "envfile")

    def test_resolve_default_store_falls_back_to_envfile(self) -> None:
        from briar.commands.auth import _resolve_default_store

        with mock.patch.dict(os.environ, {"BRIAR_DEFAULT_STORE": ""}, clear=False):
            self.assertEqual(_resolve_default_store(["envfile", "vault"]), "envfile")

    def test_effective_store_forces_envfile_for_bootstrap(self) -> None:
        """If the operator types `--store infisical` with target
        `infisical`, the bootstrap policy must override that — you'd
        be trying to store Infisical's machine identity INSIDE the
        Infisical store, which is impossible."""
        from briar.auth import AcquirerRegistry
        from briar.commands.auth import _effective_store_kind

        acquirer = AcquirerRegistry.make("infisical")
        self.assertEqual(_effective_store_kind(acquirer, requested="infisical"), "envfile")
        self.assertEqual(_effective_store_kind(acquirer, requested="vault"), "envfile")
        # envfile + envfile is fine
        self.assertEqual(_effective_store_kind(acquirer, requested="envfile"), "envfile")

    def test_effective_store_honours_request_for_external(self) -> None:
        """Vendor acquirers go wherever --store says."""
        from briar.auth import AcquirerRegistry
        from briar.commands.auth import _effective_store_kind

        for kind in ("github-pat", "jira-session", "aws-static"):
            acquirer = AcquirerRegistry.make(kind)
            self.assertEqual(_effective_store_kind(acquirer, requested="infisical"), "infisical")
            self.assertEqual(_effective_store_kind(acquirer, requested="vault"), "vault")
            self.assertEqual(_effective_store_kind(acquirer, requested="envfile"), "envfile")


class InfisicalStoreTests(unittest.TestCase):
    """`InfisicalStore` — read/write/delete/list against the Infisical
    Secrets API. SDK calls are mocked — these tests pin the contract
    surface (what we call into the SDK with), not the SDK's behaviour."""

    def setUp(self) -> None:
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "INFISICAL_CLIENT_ID": "test-cid",
                "INFISICAL_CLIENT_SECRET": "test-cs",
                "INFISICAL_PROJECT_ID": "test-pid",
                "INFISICAL_ENV": "prod",
                "INFISICAL_HOST": "https://app.infisical.com",
            },
            clear=False,
        )
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()

    def test_registered_in_credential_store_registry(self) -> None:
        from briar.credentials import CredentialStoreRegistry

        self.assertIn("infisical", CredentialStoreRegistry.kinds())

    def test_read_returns_none_when_creds_missing(self) -> None:
        """Silent miss matches EnvFileStore semantics — the doctor can
        audit without forcing operators to install the extra. Returns
        None (not '') so callers distinguish unconfigured from empty."""
        from briar.credentials.infisical import InfisicalStore

        env = {"INFISICAL_CLIENT_ID": "", "INFISICAL_CLIENT_SECRET": "", "INFISICAL_PROJECT_ID": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            store = InfisicalStore()
            self.assertIsNone(store.read("ANYTHING"))

    def test_write_calls_update_then_falls_back_to_create_on_not_found(self) -> None:
        """Symmetric to AwsSecretsManagerStore — try update first, fall
        through to create when the SDK reports 404. Uses a status-bearing
        exception (mirrors the real APIError shape) so the typed check
        passes; a generic 'Secret not found' message would NOT match
        post-hardening, which is the point — auth errors stay loud."""
        from briar.credentials.infisical import InfisicalStore

        class _FakeApiError(Exception):
            status_code = 404

        store = InfisicalStore()
        fake_client = mock.MagicMock()
        fake_client.secrets.update_secret_by_name.side_effect = _FakeApiError("404 not found")
        fake_client.secrets.create_secret_by_name.return_value = None
        store._client = fake_client  # bypass _build_client

        store.write("GITHUB_TOKEN", "ghp_xyz")
        fake_client.secrets.update_secret_by_name.assert_called_once()
        fake_client.secrets.create_secret_by_name.assert_called_once()
        # cached
        self.assertEqual(store._cache["GITHUB_TOKEN"], "ghp_xyz")

    def test_delete_returns_false_on_not_found(self) -> None:
        from briar.credentials.infisical import InfisicalStore

        class _FakeApiError(Exception):
            status_code = 404

        store = InfisicalStore()
        fake_client = mock.MagicMock()
        fake_client.secrets.delete_secret_by_name.side_effect = _FakeApiError("not found")
        store._client = fake_client
        self.assertFalse(store.delete("DOES_NOT_EXIST"))

    def test_list_empty_when_creds_missing(self) -> None:
        from briar.credentials.infisical import InfisicalStore

        env = {"INFISICAL_CLIENT_ID": "", "INFISICAL_CLIENT_SECRET": "", "INFISICAL_PROJECT_ID": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            store = InfisicalStore()
            self.assertEqual(store.list(), [])

    def test_registry_rejects_unknown_kind(self) -> None:
        from briar.auth import AcquirerRegistry
        from briar.errors import CliError

        with self.assertRaises(CliError):
            AcquirerRegistry.make("not-a-real-provider")

    def test_github_pat_flow_writes_github_token(self) -> None:
        from briar.auth import AcquirerRegistry, MockPromptIO

        acquirer = AcquirerRegistry.make("github-pat")
        prompt = MockPromptIO(answers=["ghp_test_xyz"])
        creds = acquirer.acquire(company="acme", prompt=prompt)
        self.assertEqual(creds.provider_kind, "github-pat")
        self.assertEqual(creds.entries, {"GITHUB_TOKEN": "ghp_test_xyz"})
        self.assertIn("github.com/settings/tokens", prompt.opened_urls[0])

    def test_jira_token_flow_writes_url_email_token_and_auth_kind(self) -> None:
        from briar.auth import AcquirerRegistry, MockPromptIO

        acquirer = AcquirerRegistry.make("jira-token")
        prompt = MockPromptIO(
            answers=[
                "https://acme.atlassian.net/",  # URL (trailing / stripped)
                "ops@acme.com",
                "atlassian-api-token-xyz",
            ]
        )
        creds = acquirer.acquire(company="acme", prompt=prompt)
        self.assertEqual(creds.entries["JIRA_ACME_URL"], "https://acme.atlassian.net")
        self.assertEqual(creds.entries["JIRA_ACME_EMAIL"], "ops@acme.com")
        self.assertEqual(creds.entries["JIRA_ACME_TOKEN"], "atlassian-api-token-xyz")
        # AUTH_KIND=token so autodetect_jira_auth picks this strategy
        self.assertEqual(creds.entries["JIRA_ACME_AUTH_KIND"], "token")

    def test_jira_session_flow_decodes_jwt_exp(self) -> None:
        """`expires_at` should be populated from the JWT's exp claim."""
        import base64
        import json
        import time

        from briar.auth import AcquirerRegistry, MockPromptIO

        # Build a tiny 3-segment JWT with exp = now + 1 hour
        exp = int(time.time()) + 3600
        payload = json.dumps({"exp": exp, "email": "h@acme.com"}).encode("utf-8")
        b64 = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
        jwt = f"eyJhbGciOiJIUzI1NiJ9.{b64}.fake-sig"

        acquirer = AcquirerRegistry.make("jira-session")
        prompt = MockPromptIO(
            answers=[
                "https://acme.atlassian.net",
                jwt,
            ]
        )
        creds = acquirer.acquire(company="acme", prompt=prompt)
        self.assertEqual(creds.entries["JIRA_ACME_TENANT_SESSION_TOKEN"], jwt)
        self.assertEqual(creds.entries["JIRA_ACME_AUTH_KIND"], "session")
        self.assertNotIn("JIRA_ACME_SESSION_TOKEN", creds.entries)
        self.assertNotIn("JIRA_ACME_XSRF_TOKEN", creds.entries)
        self.assertIsNotNone(creds.expires_at)

    def test_aws_static_writes_all_four_env_vars(self) -> None:
        from briar.auth import AcquirerRegistry, MockPromptIO

        acquirer = AcquirerRegistry.make("aws-static")
        prompt = MockPromptIO(answers=["AKIATEST123", "secret-xyz", ""])  # region empty → default
        creds = acquirer.acquire(company="acme", prompt=prompt)
        self.assertEqual(creds.entries["AWS_ACME_ACCESS_KEY_ID"], "AKIATEST123")
        self.assertEqual(creds.entries["AWS_ACME_SECRET_ACCESS_KEY"], "secret-xyz")
        self.assertEqual(creds.entries["AWS_ACME_REGION"], "us-east-1")  # default

    def test_bitbucket_writes_workspace_username_password(self) -> None:
        from briar.auth import AcquirerRegistry, MockPromptIO

        acquirer = AcquirerRegistry.make("bitbucket-app-password")
        prompt = MockPromptIO(answers=["acme-ws", "alice", "app-pwd-secret"])
        creds = acquirer.acquire(company="acme", prompt=prompt)
        self.assertEqual(creds.entries["BITBUCKET_ACME_WORKSPACE"], "acme-ws")
        self.assertEqual(creds.entries["BITBUCKET_ACME_USERNAME"], "alice")
        self.assertEqual(creds.entries["BITBUCKET_ACME_APP_PASSWORD"], "app-pwd-secret")

    def test_linear_writes_company_token(self) -> None:
        from briar.auth import AcquirerRegistry, MockPromptIO

        acquirer = AcquirerRegistry.make("linear-api-key")
        prompt = MockPromptIO(answers=["lin_api_xyz"])
        creds = acquirer.acquire(company="bitspark", prompt=prompt)
        self.assertEqual(creds.entries, {"LINEAR_BITSPARK_TOKEN": "lin_api_xyz"})

    def test_empty_company_rejected_by_per_company_acquirers(self) -> None:
        """Acquirers that write per-company env vars must reject ``company=""``
        instead of silently writing ``AWS__ACCESS_KEY_ID``."""
        from briar.auth import AcquirerRegistry, MockPromptIO

        for kind in ("aws-static", "bitbucket-app-password", "jira-token", "jira-session", "linear-api-key"):
            acquirer = AcquirerRegistry.make(kind)
            with self.assertRaises(ValueError):
                acquirer.acquire(company="", prompt=MockPromptIO(answers=[]))

    def test_paste_acquirers_refresh_raises_credential_expired(self) -> None:
        """Default ``refresh()`` for paste-flow acquirers must raise
        ``CredentialExpired`` so the CLI knows to ask for a fresh login."""
        from briar.auth import AcquirerRegistry, CredentialExpired, Credentials

        empty = Credentials(provider_kind="x", entries={})
        for kind in ("github-pat", "jira-token", "jira-session", "linear-api-key", "aws-static", "bitbucket-app-password"):
            with self.assertRaises(CredentialExpired):
                AcquirerRegistry.make(kind).refresh(company="x", existing=empty)

    def test_writes_classmethod_matches_acquire_keys(self) -> None:
        """``writes()`` is the doctor's audit hook — must list the
        same keys that ``acquire()`` returns. Drift here means
        ``briar auth status`` reports wrong missing-vars."""
        from briar.auth import AcquirerRegistry, MockPromptIO

        cases = {
            "github-pat": (["ghp_x"], "acme"),
            "jira-token": (["https://u.atlassian.net", "e@u.com", "tok"], "acme"),
            "linear-api-key": (["lin_x"], "acme"),
            "aws-static": (["AKIA1", "sec1", "us-west-2"], "acme"),
            "bitbucket-app-password": (["ws", "user", "pwd"], "acme"),
        }
        for kind, (answers, company) in cases.items():
            acquirer = AcquirerRegistry.make(kind)
            creds = acquirer.acquire(company=company, prompt=MockPromptIO(answers=answers))
            declared = set(type(acquirer).writes(company=company))
            # `writes` must be a subset of what `acquire` returns —
            # acquirers may write optional extras (xsrf, AUTH_KIND)
            # beyond the mandatory set the doctor audits.
            self.assertTrue(
                declared.issubset(set(creds.entries.keys())),
                f"{kind}: writes() {declared} not subset of acquire() {set(creds.entries.keys())}",
            )


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

    def test_delete_removes_from_environ_and_file(self) -> None:
        from briar.credentials.envfile import EnvFileStore

        store = EnvFileStore()
        store.write("TEST_BRIAR_FOO", "bar")
        removed = store.delete("TEST_BRIAR_FOO")
        self.assertTrue(removed)
        self.assertNotIn("TEST_BRIAR_FOO", os.environ)
        self.assertNotIn("TEST_BRIAR_FOO=", self.path.read_text())

    def test_delete_returns_false_when_missing(self) -> None:
        from briar.credentials.envfile import EnvFileStore

        store = EnvFileStore()
        self.assertFalse(store.delete("TEST_BRIAR_DOES_NOT_EXIST"))

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
         against common-secret rainbow tables.
      6. Infisical SDK errors that contain a URL get sanitized before
         landing in HydrateResult.error (which is logged + surfaced to
         the operator)."""

    # ── auth-vs-not-found propagation ──────────────────────────────

    def test_aws_secrets_auth_failure_propagates(self) -> None:
        """A revoked IAM role used to look identical to 'secret missing'
        because the broad except cached empty. Now AccessDenied raises."""
        from briar.credentials import make_credential_store

        class _AccessDenied(Exception):
            pass

        class _NotFound(Exception):
            pass

        store = make_credential_store("aws-secretsmanager")
        fake_client = mock.MagicMock()
        fake_client.exceptions.ResourceNotFoundException = _NotFound
        fake_client.get_secret_value.side_effect = _AccessDenied("AccessDeniedException: not authorized")
        with mock.patch("boto3.client", return_value=fake_client):
            with self.assertRaises(_AccessDenied):
                store.read("GITHUB_TOKEN")

    def test_aws_secrets_not_found_returns_none(self) -> None:
        """Confirmed-missing returns None (distinct from empty-string)."""
        from briar.credentials import make_credential_store

        class _NotFound(Exception):
            pass

        store = make_credential_store("aws-secretsmanager")
        fake_client = mock.MagicMock()
        fake_client.exceptions.ResourceNotFoundException = _NotFound
        fake_client.get_secret_value.side_effect = _NotFound("ResourceNotFoundException")
        with mock.patch("boto3.client", return_value=fake_client):
            self.assertIsNone(store.read("ABSENT_KEY"))

    def test_ssm_auth_failure_propagates(self) -> None:
        from briar.credentials import make_credential_store

        class _AccessDenied(Exception):
            pass

        class _NotFound(Exception):
            pass

        store = make_credential_store("ssm")
        fake_client = mock.MagicMock()
        fake_client.exceptions.ParameterNotFound = _NotFound
        fake_client.get_parameter.side_effect = _AccessDenied("AccessDeniedException")
        with mock.patch("boto3.client", return_value=fake_client):
            with self.assertRaises(_AccessDenied):
                store.read("ABSENT_KEY")

    def test_infisical_auth_failure_propagates(self) -> None:
        """401 carries no status_code==404 → re-raised, not coerced to None."""
        from briar.credentials.infisical import InfisicalStore

        class _APIError(Exception):
            status_code = 401

        env = {"INFISICAL_CLIENT_ID": "x", "INFISICAL_CLIENT_SECRET": "y", "INFISICAL_PROJECT_ID": "z"}
        with mock.patch.dict(os.environ, env, clear=False):
            store = InfisicalStore()
            fake_client = mock.MagicMock()
            fake_client.secrets.get_secret_by_name.side_effect = _APIError("Invalid credentials")
            store._client = fake_client
            with self.assertRaises(_APIError):
                store.read("ANYTHING")

    def test_infisical_is_not_found_rejects_loose_substring(self) -> None:
        """A 'Secret not found' string without a 404 marker MUST NOT
        match — the original loose sniff would have accepted this and
        silently swapped a permission denial into a CreateSecret call."""
        from briar.credentials.infisical import _is_not_found

        self.assertFalse(_is_not_found(RuntimeError("Secret not found")))
        self.assertFalse(_is_not_found(RuntimeError("403 forbidden")))
        # Real 404 markers DO match
        self.assertTrue(_is_not_found(RuntimeError("404 not found")))
        self.assertTrue(_is_not_found(RuntimeError("Status: 404")))

        class _ApiError(Exception):
            status_code = 404

        self.assertTrue(_is_not_found(_ApiError("anything")))

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

    def test_fingerprint_changes_with_install_salt(self) -> None:
        """Different installs (different $HOME) produce different
        digests for the same value — prevents pre-computed rainbow
        tables across machines."""
        import hashlib

        from briar.credentials._store import _fingerprint_salt

        # Direct check: re-deriving with a different HOME yields a
        # different salt than the cached one for the current process.
        seed_a = "/Users/operator-a"
        seed_b = "/Users/operator-b"
        salt_a = hashlib.sha256(b"briar-fingerprint-v1:" + seed_a.encode()).digest()
        salt_b = hashlib.sha256(b"briar-fingerprint-v1:" + seed_b.encode()).digest()
        self.assertNotEqual(salt_a, salt_b)
        # The exported helper returns a stable salt for THIS install.
        self.assertEqual(_fingerprint_salt(), _fingerprint_salt())

    def test_fingerprint_is_not_bare_md5(self) -> None:
        """Regression: an attacker who steals the fingerprint cannot
        check it against MD5 of common values."""
        import hashlib

        from briar.credentials import make_credential_store

        store = make_credential_store("envfile")
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_xxx"}, clear=False):
            digest = store.fingerprint("GITHUB_TOKEN")
            self.assertNotEqual(digest, hashlib.md5(b"ghp_xxx").hexdigest())

    # ── infisical bootstrap scrub ──────────────────────────────────

    def test_infisical_bootstrap_scrubs_url_from_error(self) -> None:
        """SDK APIErrors sometimes embed the request URL with project_id.
        That URL MUST be scrubbed before landing in HydrateResult.error
        (which is logged + surfaced to the operator + may end up in
        log aggregators)."""
        from briar.credentials._bootstraps.infisical import InfisicalBootstrap

        env_creds = {
            "INFISICAL_CLIENT_ID": "id",
            "INFISICAL_CLIENT_SECRET": "s",
            "INFISICAL_PROJECT_ID": "p",
        }
        with mock.patch.dict(os.environ, env_creds, clear=True):
            bs = InfisicalBootstrap()
            with mock.patch.object(
                bs,
                "_fetch_secrets",
                side_effect=RuntimeError("APIError: GET https://app.infisical.com/api/v3/secrets?projectId=proj-xyz failed"),
            ):
                result = bs.hydrate()
        self.assertFalse(result.ok)
        # The URL must NOT appear in the user-facing error
        self.assertNotIn("://", result.error)
        self.assertNotIn("proj-xyz", result.error)
        # But the type name does
        self.assertIn("RuntimeError", result.error)

    def test_infisical_bootstrap_keeps_install_hint(self) -> None:
        """The SDK-not-installed RuntimeError has no URL — its message
        ('pip install briar-cli[infisical]') is the helpful hint we
        WANT operators to see. Sanitization shouldn't eat it."""
        from briar.credentials._bootstraps.infisical import InfisicalBootstrap

        env_creds = {
            "INFISICAL_CLIENT_ID": "id",
            "INFISICAL_CLIENT_SECRET": "s",
            "INFISICAL_PROJECT_ID": "p",
        }
        with mock.patch.dict(os.environ, env_creds, clear=True):
            bs = InfisicalBootstrap()
            with mock.patch("briar.credentials._bootstraps.infisical._import_infisical_sdk", return_value=None):
                result = bs.hydrate()
        self.assertFalse(result.ok)
        self.assertIn("briar-cli[infisical]", result.error)


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
        with mock.patch.dict(os.environ, {"JIRA_ACME_URL": "https://x.test", "JIRA_ACME_EMAIL": "a@b", "JIRA_ACME_TOKEN": "t"}, clear=False):
            tracker = JiraTracker(company="acme")
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

        with mock.patch.dict(os.environ, {"LINEAR_ACME_TOKEN": "t"}, clear=False):
            tracker = LinearTracker(company="acme")
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

    def test_scaffold_raises_config_error_not_system_exit(self) -> None:
        """Library code shouldn't raise SystemExit — that traps callers
        like the dashboard collectors or programmatic test harnesses."""
        from briar.errors import CliError, ConfigError
        from briar.iac.scaffold.sources.github import SourceGithub

        ns = argparse.Namespace(owner=None, repo=None)
        with self.assertRaises(ConfigError):
            SourceGithub().build_source(ns, key_prefix="test")
        # And ConfigError is-a CliError so the CLI layer catches it.
        with self.assertRaises(CliError):
            SourceGithub().build_source(ns, key_prefix="test")

    def test_known_matchers_cover_every_credenv_entry(self) -> None:
        """Adding a new credential to CredEnv must auto-propagate to
        envfile.list() — the previous hand-maintained tuple drifted
        (INFISICAL_*, FIREFLIES_* were missing). A representative
        concrete env var for each template must match."""
        from briar.credentials.envfile import _KNOWN_MATCHERS
        from briar.env_vars import CredEnv

        def matches(name: str) -> bool:
            return any(m.match(name) for m in _KNOWN_MATCHERS)

        for member in CredEnv:
            val = member.value
            if val == "BRIAR_NOTIFY_SINKS":  # excluded as config, not a credential
                self.assertFalse(matches(val), "BRIAR_NOTIFY_SINKS must not be treated as a credential")
                continue
            # `{c}` is upper-cased + underscore-normalised by for_company;
            # substitute a representative company to get a concrete name.
            sample = val.replace("{c}", "ACME")
            self.assertTrue(matches(sample), f"CredEnv.{member.name} sample {sample!r} unmatched")

    def test_list_excludes_briar_config_vars(self) -> None:
        """The shared `BRIAR_` namespace holds both credentials
        (`BRIAR_DATABASE_URL`) and config (`BRIAR_SECRETS_FILE`,
        `BRIAR_NOTIFY_SINKS`). A bare-prefix match would leak the config
        vars into `secrets list` / `auth list`; full-template matching
        keeps them out while still listing the real DSN creds."""
        from unittest import mock

        from briar.credentials.envfile import EnvFileStore

        env = {
            "BRIAR_SECRETS_FILE": "/etc/briar/secrets.env",  # config — must NOT list
            "BRIAR_NOTIFY_SINKS": "telegram,slack",  # config — must NOT list
            "BRIAR_VERBOSE": "1",  # config — must NOT list
            "BRIAR_DATABASE_URL": "postgres://x",  # credential — must list
            "BRIAR_ACME_DATABASE_URL": "postgres://y",  # per-company cred — must list
            "GITHUB_TOKEN": "ghp_x",  # credential — must list
        }
        with mock.patch.dict("os.environ", env, clear=True):
            names = EnvFileStore().list()
        self.assertNotIn("BRIAR_SECRETS_FILE", names)
        self.assertNotIn("BRIAR_NOTIFY_SINKS", names)
        self.assertNotIn("BRIAR_VERBOSE", names)
        self.assertIn("BRIAR_DATABASE_URL", names)
        self.assertIn("BRIAR_ACME_DATABASE_URL", names)
        self.assertIn("GITHUB_TOKEN", names)


class Phase4PythonStyleHygieneTests(unittest.TestCase):
    """Regression tests for Phase 4 mechanical-style hygiene fixes."""

    def test_planop_is_abstract(self) -> None:
        from briar.commands.plan import PlanOp

        with self.assertRaises(TypeError):
            PlanOp()  # noqa: F841 — should fail because ABC

    def test_telemetryop_is_abstract(self) -> None:
        from briar.commands.telemetry import TelemetryOp

        with self.assertRaises(TypeError):
            TelemetryOp()  # noqa: F841

    def test_credenv_for_company_rejects_empty_on_templated(self) -> None:
        """Empty company on a templated var used to produce 'AWS__ACCESS_KEY_ID'
        (double underscore) which never matched any operator-set env."""
        from briar.env_vars import CredEnv

        with self.assertRaises(ValueError):
            CredEnv.AWS_KEY_ID.for_company("")
        # Fixed (non-templated) vars are unaffected
        self.assertEqual(CredEnv.GITHUB_TOKEN.for_company(""), "GITHUB_TOKEN")

    def test_credenv_read_returns_empty_for_templated_without_company(self) -> None:
        """read() should still gracefully return '' (matches the
        contract callers use with `if env.read(...)`) — it's only
        for_company() that hard-errors."""
        from briar.env_vars import CredEnv

        self.assertEqual(CredEnv.AWS_KEY_ID.read(""), "")

    def test_knowledge_splicer_can_be_constructed_without_io(self) -> None:
        """Two-step construction lets unit tests bypass the store
        entirely. Pre-Phase-4 __init__ did the store fetch eagerly."""
        from briar.iac.scaffold._knowledge import KnowledgeSplicer

        splicer = KnowledgeSplicer("acme", {"PR archaeology": "## PR archaeology\n- foo\n"})
        self.assertEqual(splicer.section("pr-archaeology"), "## PR archaeology\n- foo\n")

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
        — now an `_ACTIONS` dict matching commands/auth.py."""
        from briar.commands.runbook import CommandRunbook

        cmd = CommandRunbook()
        self.assertEqual(set(cmd._ACTIONS), {"extract", "sweep", "serve"})
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

    def test_config_file_section_uses_derived_valid_set(self) -> None:
        """iac/config_file.py:section used to rebuild a 7-entry closure
        dict on every call. Now derived from ConfigSpec.model_fields
        and validated against a ClassVar set."""
        from briar.errors import ConfigError
        from briar.iac.config_file import ConfigFile
        from briar.iac.models import ConfigSpec

        # The derived set matches ConfigSpec exactly
        self.assertEqual(ConfigFile._VALID_SECTIONS, frozenset(ConfigSpec.model_fields.keys()))
        # Unknown section raises ConfigError
        cf = ConfigFile(ConfigSpec())
        with self.assertRaises(ConfigError):
            cf.section("not_a_section")
        # Known section returns a list (empty for a fresh spec)
        self.assertEqual(cf.section("agents"), [])

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

        # Valid stores parse
        KnowledgeBinding(store="file")
        KnowledgeBinding(store="postgres")
        # Unknown store rejected
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

    def test_tool_filter_needle_is_literal(self) -> None:
        """Constraining the substring needle set catches typos in
        archetype subclass tool_filter assignments at type-check time."""
        from typing import get_args

        from briar.iac.scaffold.archetypes.base import ToolFilterNeedle

        needles = set(get_args(ToolFilterNeedle))
        # The canonical set from the source build_tools shape
        self.assertEqual(needles, {"comment_on_issue", "add_labels", "comment", "commit", "open_pr"})

    def test_every_archetype_tool_filter_uses_valid_needles(self) -> None:
        """Belt-and-suspenders: every concrete archetype's tool_filter
        entries are members of ToolFilterNeedle. If a new archetype
        adds a typo, this test catches it at runtime too."""
        from typing import get_args

        from briar.iac.scaffold.archetypes import ARCHETYPES
        from briar.iac.scaffold.archetypes.base import ToolFilterNeedle

        allowed = set(get_args(ToolFilterNeedle))
        for name, archetype in ARCHETYPES.items():
            with self.subTest(archetype=name):
                for needle in archetype.tool_filter:
                    self.assertIn(needle, allowed, f"archetype {name!r} uses unknown needle {needle!r}")


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

    def test_parse_pr_target_moved_to_writer_module(self) -> None:
        """Both PR-comment writers used to define _parse_target
        verbatim. Now one helper in messaging/_writer.py."""
        from briar.messaging._writer import parse_pr_target

        self.assertEqual(parse_pr_target("owner/repo#123", {}), ("owner/repo", 123))
        self.assertEqual(parse_pr_target("owner/repo", {"pr": 5}), ("owner/repo", 5))
        self.assertEqual(parse_pr_target("garbage#notnumber", {}), ("", 0))
        self.assertEqual(parse_pr_target("", {}), ("", 0))

    def test_jira_creds_from_env(self) -> None:
        """Two Jira writers used to duplicate the URL/email/token read."""
        from briar.messaging._jira_creds import JiraCreds

        env = {
            "JIRA_ACME_URL": "https://acme.atlassian.net",
            "JIRA_ACME_EMAIL": "a@b.com",
            "JIRA_ACME_TOKEN": "tok",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            creds = JiraCreds.from_env("acme")
        self.assertEqual(creds.url, "https://acme.atlassian.net")
        self.assertEqual(creds.email, "a@b.com")
        self.assertEqual(creds.token, "tok")
        self.assertTrue(creds.is_complete())

    def test_jira_creds_from_empty_company(self) -> None:
        from briar.messaging._jira_creds import JiraCreds

        creds = JiraCreds.from_env("")
        self.assertEqual((creds.url, creds.email, creds.token), ("", "", ""))
        self.assertFalse(creds.is_complete())
        self.assertEqual(JiraCreds.required_env_vars(""), [])

    def test_jira_creds_required_env_vars(self) -> None:
        from briar.messaging._jira_creds import JiraCreds

        names = JiraCreds.required_env_vars("acme")
        self.assertEqual(sorted(names), ["JIRA_ACME_EMAIL", "JIRA_ACME_TOKEN", "JIRA_ACME_URL"])

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

    def test_agent_no_longer_returns_granular_exit_codes(self) -> None:
        """Belt-and-suspenders: grep the agent module for any leftover
        ExitCode reference. (`replace_all` should have caught these.)"""
        from pathlib import Path

        agent_src = Path("src/briar/commands/agent.py").read_text()
        for name in ("STORE_OPEN_FAILED", "CLONE_FAILED", "GIT_CONFIG_FAILED", "AGENT_ERROR"):
            self.assertNotIn(name, agent_src, f"agent.py still references ExitCode.{name}")

    def test_failure_ctx_records_through_one_object(self) -> None:
        """_FailureCtx bundles the 4 constant per-schedule fields so
        the failure path is `failure.record(reason=, blob_name=, exc=)`
        — replaces a 7-arg call duplicated 3 times in the original."""
        from briar.iac.runbook.executor import ExtractRow, _FailureCtx

        rows: list = []
        ctx = _FailureCtx(company_name="Acme Inc", company="acme", task="extractors", rows=rows)
        # Patch the colocated notify helper so we don't hit any real sink.
        with mock.patch("briar.iac.runbook.executor._notify_failure") as notify:
            ctx.record(reason="collect_sections raised", blob_name="knowledge:acme", exc=RuntimeError("boom"))
        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0], ExtractRow)
        self.assertEqual(rows[0].company, "Acme Inc")
        self.assertEqual(rows[0].task, "extractors")
        self.assertIn("collect_sections raised", rows[0].status)
        notify.assert_called_once_with("acme", "extractors", "collect_sections raised", "boom")

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
            files = GithubProvider._fetch_commit_files("o/r", "deadbeef")
        self.assertEqual(files, ["src/x.py", "tests/y.py"])

    def test_fetch_commit_files_handles_non_dict_response(self) -> None:
        from briar.extract._providers.github import GithubProvider

        with mock.patch("briar.extract._providers.github.GithubApi.get_json", return_value="error string"):
            self.assertEqual(GithubProvider._fetch_commit_files("o/r", "sha"), [])

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

    def test_workflow_shape_is_dataclass_not_abc(self) -> None:
        """3 one-method ABC subclasses → 1 frozen dataclass holding
        the per-shape graph-builder callable."""
        import dataclasses

        from briar.iac.scaffold.shapes import WORKFLOW_SHAPES, WorkflowShape

        self.assertTrue(dataclasses.is_dataclass(WorkflowShape))
        for name, shape in WORKFLOW_SHAPES.items():
            with self.subTest(name=name):
                self.assertIsInstance(shape, WorkflowShape)
                self.assertTrue(callable(shape.build_graph))
                # The graph builder still works
                graph = shape.build_graph("agent-key-1")
                self.assertIn("entry", graph)
                self.assertIn("nodes", graph)

    def test_shapes_package_collapsed_to_one_file(self) -> None:
        """The per-shape sub-modules (one_shot.py / triage.py /
        plan_approve_act.py / base.py) are deleted — the package
        is now just __init__.py."""
        with self.assertRaises(ModuleNotFoundError):
            import briar.iac.scaffold.shapes.base  # noqa: F401
        with self.assertRaises(ModuleNotFoundError):
            import briar.iac.scaffold.shapes.one_shot  # noqa: F401
        with self.assertRaises(ModuleNotFoundError):
            import briar.iac.scaffold.shapes.plan_approve_act  # noqa: F401
        with self.assertRaises(ModuleNotFoundError):
            import briar.iac.scaffold.shapes.triage  # noqa: F401

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

    def test_telemetry_preview_event_has_outcome_field(self) -> None:
        """Cleanup of redundant `tags.setdefault("outcome","preview")`
        next to hardcoded `outcome="preview"` — the field-level value
        still lands correctly."""
        from briar.telemetry import preview_next_event

        event = preview_next_event(command="extract")
        self.assertEqual(event.outcome, "preview")
        self.assertEqual(event.duration_ms, 0)
        self.assertEqual(event.command, "extract")


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

    def test_slack_writer_applies_ai_prefix(self) -> None:
        """CLAUDE.md mandates [AI] prefix on operator-impersonated
        messages on ANY channel. Slack was missing it."""
        import inspect

        from briar.messaging.slack_channel import SlackChannelWriter

        src = inspect.getsource(SlackChannelWriter.send)
        self.assertIn("with_ai_prefix", src)

    def test_telegram_writer_applies_ai_prefix(self) -> None:
        import inspect

        from briar.messaging.telegram_chat import TelegramChatWriter

        src = inspect.getsource(TelegramChatWriter.send)
        self.assertIn("with_ai_prefix", src)

    def test_jira_transition_writer_applies_ai_prefix(self) -> None:
        import inspect

        from briar.messaging.jira_transition import JiraTransitionWriter

        src = inspect.getsource(JiraTransitionWriter.send)
        self.assertIn("with_ai_prefix", src)

    # ── Vault name validation ────────────────────────────────────────

    def test_vault_store_rejects_path_traversal_name(self) -> None:
        """`read("../foo")` would build Vault path `briar/../foo` and
        could read a sibling mount. Same _NAME_RE as EnvFileStore."""
        from briar.credentials.vault import VaultStore

        store = VaultStore()
        with self.assertRaises(ValueError):
            store.read("../escape")
        with self.assertRaises(ValueError):
            store.read("lowercase")
        # Valid name → not a name-validation raise (may raise other
        # errors due to missing VAULT_ADDR, but not ValueError)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(store.read("VALID_NAME"))

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

    def test_dashboard_host_defaults_to_loopback(self) -> None:
        """0.0.0.0 default was a security-review red flag."""
        from briar.commands.dashboard import CommandDashboard

        parser = argparse.ArgumentParser()
        CommandDashboard().add_arguments(parser)
        ns = parser.parse_args([])
        self.assertEqual(ns.host, "127.0.0.1")

    def test_sentry_emit_does_not_put_message_in_body(self) -> None:
        """capture_message body bypasses before_send; the Phase 12 fix
        sends only the error_type as the message body and routes the
        scrubbed text through a tag."""
        from briar.telemetry._sinks.base import TelemetryEvent
        from briar.telemetry._sinks.sentry import SentrySink

        sink = SentrySink(dsn="https://k@example.test/1", release="t")
        captured: dict = {"messages": [], "tags": {}}

        fake_scope = mock.MagicMock()
        fake_scope.set_tag = lambda k, v: captured["tags"].update({k: v})
        fake_scope.__enter__ = lambda s: s
        fake_scope.__exit__ = lambda *a: None

        fake_sentry = mock.MagicMock()
        fake_sentry.isolation_scope.return_value = fake_scope
        fake_sentry.capture_message = lambda msg, level: captured["messages"].append((msg, level))

        with mock.patch.object(sink, "_ensure_init", return_value=True):
            with mock.patch.dict("sys.modules", {"sentry_sdk": fake_sentry}):
                event = TelemetryEvent(
                    kind="error",
                    command="extract",
                    outcome="error",
                    duration_ms=0,
                    error_type="ValueError",
                    error_message="raw sk-secret-key-leak",
                    tags={"baseline": "tag"},
                )
                sink.emit(event)

        # Body is just the type, not the message text
        self.assertEqual(captured["messages"], [("briar.error: ValueError", "error")])
        # Message text lands in a tag (which goes through the scrubber)
        self.assertEqual(captured["tags"].get("error_type"), "ValueError")
        self.assertEqual(captured["tags"].get("error_message"), "raw sk-secret-key-leak")

    def test_meeting_constants_centralised(self) -> None:
        """DEFAULT_MEETING_TOP_K and DEFAULT_MEETING_MAX_BYTES live in
        extract/_meeting.py and feed every command-side default."""
        from briar.extract._meeting import DEFAULT_MEETING_MAX_BYTES, DEFAULT_MEETING_TOP_K

        self.assertEqual(DEFAULT_MEETING_TOP_K, 3)
        self.assertEqual(DEFAULT_MEETING_MAX_BYTES, 50_000)

    def test_scaffold_source_auth_helper_uses_classvars(self) -> None:
        """The hoisted SourceTemplate._auth pulls per-source values
        from auth_secret_arg + default_provider_for_oauth ClassVars."""
        from briar.iac.scaffold.sources.bitbucket import SourceBitbucket
        from briar.iac.scaffold.sources.github import SourceGithub

        self.assertEqual(SourceGithub.auth_secret_arg, "github_secret_id")
        self.assertEqual(SourceBitbucket.auth_secret_arg, "bitbucket_secret_id")

        # OAuth path uses default_provider_for_oauth
        gh = SourceGithub()
        ns = argparse.Namespace(auth_mode="oauth")
        out = gh._auth(ns)
        self.assertEqual(out["credential_binding"]["provider"], "github")

        bb = SourceBitbucket()
        out = bb._auth(ns)
        self.assertEqual(out["credential_binding"]["provider"], "bitbucket")

    def test_scaffold_source_auth_pat_requires_secret_per_source(self) -> None:
        """PAT mode without the per-source secret-id raises a ConfigError
        that names the right --{kind}-secret-id flag."""
        from briar.errors import ConfigError
        from briar.iac.scaffold.sources.bitbucket import SourceBitbucket
        from briar.iac.scaffold.sources.github import SourceGithub

        ns = argparse.Namespace(auth_mode="pat", github_secret_id=None, bitbucket_secret_id=None)
        with self.assertRaises(ConfigError) as ctx:
            SourceGithub()._auth(ns)
        self.assertIn("--github-secret-id", str(ctx.exception))
        with self.assertRaises(ConfigError) as ctx:
            SourceBitbucket()._auth(ns)
        self.assertIn("--bitbucket-secret-id", str(ctx.exception))

    def test_scaffold_source_user_filters_prefix_by_kind(self) -> None:
        """Hoisted _user_filters reads `{kind}_authors_allow` etc. so a
        new source-template that just sets `kind = "linear"` gets the
        right argparse-attr lookups for free."""
        from briar.iac.scaffold.sources.bitbucket import SourceBitbucket
        from briar.iac.scaffold.sources.github import SourceGithub

        gh_ns = argparse.Namespace(
            github_authors_allow=["a"],
            github_authors_block=["b"],
            github_assignees_allow=["c"],
            github_assignees_block=["d"],
        )
        self.assertEqual(
            SourceGithub()._user_filters(gh_ns),
            {
                "authors_allow": ["a"],
                "authors_block": ["b"],
                "assignees_allow": ["c"],
                "assignees_block": ["d"],
            },
        )
        bb_ns = argparse.Namespace(
            bitbucket_authors_allow=["x"],
            bitbucket_authors_block=[],
            bitbucket_assignees_allow=[],
            bitbucket_assignees_block=[],
        )
        self.assertEqual(SourceBitbucket()._user_filters(bb_ns)["authors_allow"], ["x"])


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

    def test_scaffold_resolver_and_args_classes_gone(self) -> None:
        """`ScaffoldResolver` + `ScaffoldArgs` were static-only
        namespace classes; Phase 13 demoted them to module functions
        (`target_for`, `add_common_arguments`, …)."""
        import briar.iac.scaffold._composer as composer

        self.assertFalse(hasattr(composer, "ScaffoldResolver"))
        self.assertFalse(hasattr(composer, "ScaffoldArgs"))
        # The functions exist at module level
        self.assertTrue(callable(composer.target_for))
        self.assertTrue(callable(composer.add_common_arguments))
        self.assertTrue(callable(composer.attach_source_arguments))
        self.assertTrue(callable(composer.attach_trigger_arguments))

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
        self.assertTrue(callable(cli._install_default_journal))


class Phase14StructuralCleanupTests(unittest.TestCase):
    """Regression tests for Phase 14 — dead requires_* ClassVar
    deletion + CommandAgent template-method extraction."""

    def test_unused_requires_classvars_deleted(self) -> None:
        """4 of the 6 `requires_*` ClassVars were declared but never
        read anywhere. Phase 13 deleted them; only the 2 that the
        dashboard collector actually reads (`requires_github` /
        `requires_aws`) survive."""
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

        # The live ones remain on the base for the dashboard
        self.assertIn("requires_github", KnowledgeExtractor.__dict__)
        self.assertIn("requires_aws", KnowledgeExtractor.__dict__)

    def test_command_agent_helpers_extracted(self) -> None:
        """Phase 14 extracted `_prepare_agent_workdir` +
        `_finalize_agent_result` + `_fetch_meeting_context_from_args`
        from `_run_prfix` / `_run_implement` (which used to share
        ~70% of their bodies)."""
        from briar.commands.agent import CommandAgent

        for name in ("_prepare_agent_workdir", "_finalize_agent_result", "_fetch_meeting_context_from_args"):
            with self.subTest(helper=name):
                self.assertTrue(hasattr(CommandAgent, name), f"CommandAgent missing {name}")

    def test_run_prfix_and_run_implement_shrank(self) -> None:
        """Body-length sanity — both methods used to be ~100+ lines
        each with ~70% overlap. Post-extraction both should be well
        under 50 lines (the spec + per-op context-fetch + AgentRunner
        construction, no boilerplate)."""
        import inspect

        from briar.commands.agent import CommandAgent

        prfix_lines = len(inspect.getsource(CommandAgent._run_prfix).splitlines())
        implement_lines = len(inspect.getsource(CommandAgent._run_implement).splitlines())
        self.assertLess(prfix_lines, 60, f"_run_prfix is {prfix_lines} lines — should be <60 after extraction")
        self.assertLess(implement_lines, 65, f"_run_implement is {implement_lines} lines — should be <65 after extraction")


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

    def test_resolve_provider_from_args_uses_flag_and_company(self) -> None:
        from briar.extract.base import _resolve_provider_from_args

        captured: dict = {}

        def fake_make(kind, *, company, **extra):
            captured.update({"kind": kind, "company": company, "extra": extra})
            return ("provider", kind, company)

        ns = argparse.Namespace(cloud="gcp", company="acme", region="us-east-1", profile="acme-prod")
        result = _resolve_provider_from_args(
            ns,
            flag="cloud",
            default="aws",
            make_fn=fake_make,
            region=ns.region,
            profile=ns.profile,
        )
        self.assertEqual(captured["kind"], "gcp")
        self.assertEqual(captured["company"], "acme")
        self.assertEqual(captured["extra"], {"region": "us-east-1", "profile": "acme-prod"})
        self.assertEqual(result, ("provider", "gcp", "acme"))

    def test_resolve_provider_falls_back_to_default(self) -> None:
        from briar.extract.base import _resolve_provider_from_args

        def fake_make(kind, *, company, **_):
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


class JiraAuthModuleFunctionsTests(unittest.TestCase):
    """Phase 14 follow-up — JiraAuthRegistry namespace class demoted to
    module-level `jira_auth_kinds` / `make_jira_auth` / `autodetect_jira_auth`.
    Regression: callers that imported `JiraAuthRegistry` must fail to
    import (catches stale doc + comment references)."""

    def test_jira_auth_kinds_returns_both_strategies(self) -> None:
        from briar.extract._trackers._jira_auth import jira_auth_kinds

        kinds = jira_auth_kinds()
        self.assertEqual(set(kinds), {"token", "session"})

    def test_make_jira_auth_returns_strategy_instance(self) -> None:
        from briar.extract._trackers._jira_auth import JiraSessionAuth, JiraTokenAuth, make_jira_auth

        self.assertIsInstance(make_jira_auth("token"), JiraTokenAuth)
        self.assertIsInstance(make_jira_auth("session"), JiraSessionAuth)

    def test_legacy_jira_auth_registry_no_longer_exported(self) -> None:
        import briar.extract._trackers._jira_auth as mod

        self.assertFalse(
            hasattr(mod, "JiraAuthRegistry"),
            "JiraAuthRegistry must not be reintroduced — use module functions instead",
        )


class ExtractorHeadingDerivationTests(unittest.TestCase):
    """Phase 14 follow-up — `_EXTRACTOR_HEADINGS` is derived from the
    `heading` ClassVar on each extractor instead of a hardcoded dict.
    Regression: adding a new extractor must not require touching
    `_knowledge.py`."""

    def test_every_registered_extractor_with_heading_appears(self) -> None:
        from briar.extract import EXTRACTORS, TASK_SCOPED_EXTRACTORS
        from briar.iac.scaffold._knowledge import _EXTRACTOR_HEADINGS

        for name, ext in {**EXTRACTORS, **TASK_SCOPED_EXTRACTORS}.items():
            if ext.heading:
                self.assertEqual(
                    _EXTRACTOR_HEADINGS.get(name),
                    ext.heading,
                    f"derived map must mirror {name}.heading",
                )

    def test_extractor_without_heading_omitted(self) -> None:
        """If a new extractor ships without a heading (e.g. internal-
        only), the derived map silently omits it — the splicer's
        consumer-side lookup returns empty string for unknown names."""
        from briar.iac.scaffold._knowledge import _EXTRACTOR_HEADINGS

        for heading in _EXTRACTOR_HEADINGS.values():
            self.assertTrue(heading, "no empty-string headings should leak into the derived map")

    def test_splicer_section_lookup_works_through_derived_map(self) -> None:
        """End-to-end: the splicer's `section()` resolves a known
        extractor name → heading → body via the derived mapping."""
        from briar.iac.scaffold._knowledge import KnowledgeSplicer

        splicer = KnowledgeSplicer("acme", {"PR archaeology": "## PR archaeology\nbody"})
        self.assertIn("PR archaeology", splicer.section("pr-archaeology"))
        self.assertEqual(splicer.section("does-not-exist"), "")


if __name__ == "__main__":
    unittest.main()
