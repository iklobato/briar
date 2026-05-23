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
from unittest import mock


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
            self.assertEqual(store.read("DOES_NOT_EXIST"), "")

    def test_envfile_list_filters_to_known_prefixes(self) -> None:
        from briar.credentials import make_credential_store

        store = make_credential_store("envfile")
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "x", "BITBUCKET_ACME_USERNAME": "u", "UNRELATED_VAR": "y"}, clear=True):
            names = store.list()
            self.assertIn("GITHUB_TOKEN", names)
            self.assertIn("BITBUCKET_ACME_USERNAME", names)
            self.assertNotIn("UNRELATED_VAR", names)

    def test_envfile_fingerprint_is_md5(self) -> None:
        import hashlib

        from briar.credentials import make_credential_store

        store = make_credential_store("envfile")
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_xxx"}, clear=False):
            expected = hashlib.md5(b"ghp_xxx").hexdigest()
            self.assertEqual(store.fingerprint("GITHUB_TOKEN"), expected)

    def test_aws_secrets_read_uses_boto3_and_caches(self) -> None:
        from briar.credentials import make_credential_store

        store = make_credential_store("aws-secretsmanager")
        fake_client = mock.MagicMock()
        fake_client.get_secret_value.return_value = {"SecretString": "ghp_xxx"}
        with mock.patch("boto3.client", return_value=fake_client):
            self.assertEqual(store.read("GITHUB_TOKEN"), "ghp_xxx")
            self.assertEqual(store.read("GITHUB_TOKEN"), "ghp_xxx")  # cached
            self.assertEqual(fake_client.get_secret_value.call_count, 1)
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
            self.assertEqual(store.read("anything"), "")  # silent miss, no SDK call attempted

    def test_vault_read_raises_when_hvac_missing(self) -> None:
        from briar.credentials import make_credential_store

        with mock.patch.dict("os.environ", {"VAULT_ADDR": "http://x", "VAULT_TOKEN": "t"}):
            store = make_credential_store("vault")
            with mock.patch("briar.credentials.vault._import_hvac", return_value=None):
                with self.assertRaises(RuntimeError) as ctx:
                    store.read("anything")
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
    """`RunbookExtractor._notify_failure` dispatches to every sink
    listed in ``$BRIAR_NOTIFY_SINKS``. The dispatch must NOT raise
    — a broken sink can't crash the scheduler."""

    def test_notify_failure_dispatches_to_telegram_when_configured(self) -> None:
        from briar.iac.runbook.executor import RunbookExtractor

        fake_sink = mock.MagicMock()
        fake_sink.is_available.return_value = True
        fake_sink.send.return_value = True

        with mock.patch.dict("os.environ", {"BRIAR_NOTIFY_SINKS": "telegram"}):
            with mock.patch("briar.notify.make_sink", return_value=fake_sink):
                RunbookExtractor._notify_failure("acme", "extractors", "stuff broke", "trace")

        self.assertEqual(fake_sink.send.call_count, 1)
        kwargs = fake_sink.send.call_args.kwargs
        self.assertIn("acme", kwargs["title"])
        self.assertIn("stuff broke", kwargs["body"])

    def test_notify_failure_silent_when_no_sinks_configured(self) -> None:
        from briar.iac.runbook.executor import RunbookExtractor

        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("briar.notify.make_sink") as make_sink:
                RunbookExtractor._notify_failure("acme", "extractors", "stuff broke", "trace")
                self.assertEqual(make_sink.call_count, 0)

    def test_notify_failure_swallows_sink_exceptions(self) -> None:
        from briar.iac.runbook.executor import RunbookExtractor

        fake_sink = mock.MagicMock()
        fake_sink.is_available.return_value = True
        fake_sink.send.side_effect = RuntimeError("network down")

        with mock.patch.dict("os.environ", {"BRIAR_NOTIFY_SINKS": "telegram"}):
            with mock.patch("briar.notify.make_sink", return_value=fake_sink):
                # MUST NOT raise — scheduler stays alive on sink failure
                RunbookExtractor._notify_failure("acme", "extractors", "x", "y")


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
        from briar.messaging.github_pr_comment import GithubPrCommentWriter

        # `#`-form
        repo, n = GithubPrCommentWriter._parse_target("acme/app#42", {})
        self.assertEqual((repo, n), ("acme/app", 42))
        # extras form
        repo, n = GithubPrCommentWriter._parse_target("acme/app", {"pr": 7})
        self.assertEqual((repo, n), ("acme/app", 7))
        # Garbage
        repo, n = GithubPrCommentWriter._parse_target("nonsense", {})
        self.assertEqual((repo, n), ("", 0))

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

    def test_registry_lists_infisical(self) -> None:
        from briar.credentials._bootstraps import BOOTSTRAPS

        self.assertIn("infisical", BOOTSTRAPS)

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
        """No backend has its creds set → returns a `(none)` result.
        Does NOT raise — startup must be robust to "no remote vault"."""
        from briar.credentials._bootstraps import auto_bootstrap

        with mock.patch.dict("os.environ", {}, clear=True):
            result = auto_bootstrap()
        self.assertEqual(result.backend, "(none)")
        self.assertEqual(result.count, 0)
        self.assertTrue(result.ok)

    def test_infisical_hydrate_writes_via_setdefault_dry_run(self) -> None:
        """Dry-run path: fetches from Infisical (mocked SDK), reports
        the keys that WOULD be set, never writes to os.environ.
        Already-set env vars are listed in `skipped`."""
        from briar.credentials._bootstraps.infisical import InfisicalBootstrap

        fake_secret = mock.MagicMock(secretKey="NEW_VAR", secretValue="x")
        already_set = mock.MagicMock(secretKey="GITHUB_TOKEN", secretValue="from-vault")
        fake_result = mock.MagicMock(secrets=[fake_secret, already_set])

        env_creds = {
            "INFISICAL_CLIENT_ID": "id-x",
            "INFISICAL_CLIENT_SECRET": "secret-x",
            "INFISICAL_PROJECT_ID": "proj-x",
            "GITHUB_TOKEN": "operator-supplied-token",   # would be preserved
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
        self.assertIn("GITHUB_TOKEN", result.skipped)   # operator-supplied wins
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


class RepoClonerRegistryTests(unittest.TestCase):
    """`_clone_default` + `_implement_specific_instructions` dispatch
    via the REPO_CLONERS registry. Adding a new provider must NOT
    require editing those methods."""

    def test_clone_registry_has_both_providers(self) -> None:
        from briar.commands.agent import REPO_CLONERS

        self.assertIn("github", REPO_CLONERS)
        self.assertIn("bitbucket", REPO_CLONERS)

    def test_github_cloner_uses_x_access_token_url(self) -> None:
        from briar.commands.agent import REPO_CLONERS

        c = REPO_CLONERS["github"]
        self.assertEqual(c.clone_url("acme", "app"), "https://github.com/acme/app.git")
        self.assertEqual(
            c.authed_clone_url("acme", "app", "ghp_xxx"),
            "https://x-access-token:ghp_xxx@github.com/acme/app.git",
        )

    def test_bitbucket_cloner_uses_x_token_auth_url(self) -> None:
        from briar.commands.agent import REPO_CLONERS

        c = REPO_CLONERS["bitbucket"]
        self.assertEqual(c.clone_url("acme", "app"), "https://bitbucket.org/acme/app.git")
        self.assertEqual(
            c.authed_clone_url("acme", "app", "ATBB-xxx"),
            "https://x-token-auth:ATBB-xxx@bitbucket.org/acme/app.git",
        )

    def test_pr_recipe_dispatches_to_provider(self) -> None:
        """If anyone reverts to `if provider == 'bitbucket':` in
        _implement_specific_instructions, this test catches it — we
        swap the github recipe via mock.patch.dict and confirm the
        substitution made it into the rendered instructions."""
        from briar.commands.agent import REPO_CLONERS, CommandAgent

        class FakeCloner:
            kind = "github"

            def pr_creation_recipe(self, *, owner, repo, branch, company):
                return "  6. DO THE FAKE THING.\n  7. DONE.\n"

        with mock.patch.dict(REPO_CLONERS, {"github": FakeCloner()}):
            text = CommandAgent._implement_specific_instructions(
                provider="github", company="acme", owner="acme", repo="app", ticket_key="ACME-1"
            )
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
            sections = CommandAgent._fetch_ticket_context(
                company="acme", tracker="jira", ticket_project="ACME", ticket_key="ACME-42"
            )
        self.assertEqual(sections, [])

    def test_dry_run_skips_llm_call_and_returns_marker(self) -> None:
        """`AgentRunner(dry_run=True).run()` prints the rendered prompt
        and returns without invoking the LLM. The LLM provider's
        is_available() is NOT checked — we want to render the prompt
        even on hosts without LLM creds (that's the whole point)."""
        import io
        import sys
        from pathlib import Path

        from briar.agent.runner import AgentRunner

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
            company="acme",
            task="implement",
            archetype_name="engineer",
            workdir=Path("/tmp/briar-test"),
            knowledge_store=fake_store,
            target="acme-co/acme-app",
            llm=fake_llm,
            dry_run=True,
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

        instructions = CommandAgent._implement_specific_instructions(
            provider="github", company="acme", owner="acme-co", repo="acme-app", ticket_key="ACME-42"
        )
        self.assertIn("ACME-42", instructions)
        self.assertIn("briar/acme-42", instructions)
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
        from briar.extract._trackers._jira_auth import JiraAuthRegistry

        kinds = JiraAuthRegistry.kinds()
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
        from briar.extract._trackers._jira_auth import JiraAuthRegistry, JiraSessionAuth

        env = {
            "JIRA_ACME_EMAIL": "ops@acme.com",
            "JIRA_ACME_TOKEN": "tok-123",
            "JIRA_ACME_SESSION_TOKEN": "cookie-val",
            "JIRA_ACME_AUTH_KIND": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            auth = JiraAuthRegistry.autodetect(company="acme")
            self.assertIsInstance(auth, JiraSessionAuth)

    def test_autodetect_falls_back_to_token_when_no_session(self) -> None:
        from briar.extract._trackers._jira_auth import JiraAuthRegistry, JiraTokenAuth

        env = {
            "JIRA_ACME_EMAIL": "ops@acme.com",
            "JIRA_ACME_TOKEN": "tok-123",
            "JIRA_ACME_SESSION_TOKEN": "",
            "JIRA_ACME_AUTH_KIND": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            auth = JiraAuthRegistry.autodetect(company="acme")
            self.assertIsInstance(auth, JiraTokenAuth)

    def test_explicit_auth_kind_overrides_autodetect(self) -> None:
        from briar.extract._trackers._jira_auth import JiraAuthRegistry, JiraTokenAuth

        # Session token IS set, but operator forces token via env var
        env = {
            "JIRA_ACME_SESSION_TOKEN": "cookie-val",
            "JIRA_ACME_AUTH_KIND": "token",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            auth = JiraAuthRegistry.autodetect(company="acme")
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
        from briar.extract._trackers._jira_auth import JiraTokenAuth, JiraSessionAuth
        from briar.extract._trackers.jira import JiraTracker

        t1 = JiraTracker(company="acme", auth_kind="session")
        self.assertIsInstance(t1._auth, JiraSessionAuth)
        t2 = JiraTracker(company="acme", auth_kind="token")
        self.assertIsInstance(t2._auth, JiraTokenAuth)


if __name__ == "__main__":
    unittest.main()
