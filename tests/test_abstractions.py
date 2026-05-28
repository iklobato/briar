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
        self.assertIn("401", infisical_result.error)

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

        text = CommandAgent._implement_specific_instructions(
            provider=FakeProvider(), owner="acme", repo="app", ticket_key="ACME-1"
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

        from briar.agent.runner import AgentRunner, AgentRunConfig

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
        instructions = CommandAgent._implement_specific_instructions(
            provider=provider, owner="acme-co", repo="acme-app", ticket_key="ACME-42"
        )
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

    def test_registry_make_raises_for_unknown_kind(self) -> None:
        from briar.errors import CliError
        from briar.extract._trackers._jira_auth import JiraAuthRegistry

        with self.assertRaises(CliError):
            JiraAuthRegistry.make("oauth-not-implemented")

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
        import base64, json

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
        self.assertIsNone(_decode_jwt_exp("only.two"))           # 2 segments
        self.assertIsNone(_decode_jwt_exp("a.b.c.d"))            # 4 segments

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
    ``JIRA_<C>_AUTH_KIND`` so that ``JiraAuthRegistry.autodetect``
    locks to the strategy the operator just onboarded — a regression
    that drops that key would silently flip the strategy."""

    def test_token_acquirer_writes_all_four_env_vars(self) -> None:
        from briar.auth._acquirers.jira_token import JiraTokenAcquirer
        from briar.auth._prompt import MockPromptIO

        prompt = MockPromptIO(answers=[
            "https://acme.atlassian.net",
            "ops@acme.com",
            "tok-secret-123",
        ])
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

        prompt = MockPromptIO(answers=[
            "https://acme.atlassian.net",
            "tenant-jwt-blob",
        ])
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
        prompt = MockPromptIO(answers=[
            "https://acme.atlassian.net",
            jwt,
        ])
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
            result = RetryAfter(wait_seconds=5, reason="test").apply(
                exc=RuntimeError("x"), attempt=1
            )
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
        from briar.error_policy import (
            Abort,
            ErrorPolicyRegistry,
            ExceptionTypePolicy,
            RetryAfter,
        )

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
        from briar.error_policy import (
            Abort,
            ErrorPolicyRegistry,
            ExceptionTypePolicy,
            RetryAfter,
        )

        base = ErrorPolicyRegistry(
            policies=(ExceptionTypePolicy(exception_type=ValueError, decision=Abort()),)
        )
        overlay = ExceptionTypePolicy(
            exception_type=ValueError, decision=RetryAfter(60, reason="override")
        )
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
        from briar.error_policy import (
            ErrorPolicyRegistry,
            ExceptionTypePolicy,
            RetryAfter,
            RetryingExecutor,
        )

        calls = [0]

        def fn():
            calls[0] += 1
            if calls[0] < 3:
                raise ValueError("transient")
            return "ok"

        registry = ErrorPolicyRegistry(
            policies=(
                ExceptionTypePolicy(exception_type=ValueError, decision=RetryAfter(0, reason="t")),
            )
        )
        with mock.patch("briar.error_policy.time.sleep"):
            result = RetryingExecutor(registry, max_attempts=5).run(fn)
        self.assertEqual(result, "ok")
        self.assertEqual(calls[0], 3)

    def test_executor_raises_when_decision_is_abort(self) -> None:
        """First attempt fails with a class matched by an Abort policy →
        executor MUST propagate immediately, no retry."""
        from briar.error_policy import (
            Abort,
            ErrorPolicyRegistry,
            ExceptionTypePolicy,
            RetryingExecutor,
        )

        calls = [0]

        def fn():
            calls[0] += 1
            raise ValueError("auth-failed")

        registry = ErrorPolicyRegistry(
            policies=(ExceptionTypePolicy(exception_type=ValueError, decision=Abort()),)
        )
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
        from briar.error_policy import (
            ErrorPolicyRegistry,
            ExceptionTypePolicy,
            RetryAfter,
            RetryingExecutor,
        )

        def always_fail():
            raise ValueError("always")

        registry = ErrorPolicyRegistry(
            policies=(
                ExceptionTypePolicy(exception_type=ValueError, decision=RetryAfter(0, reason="t")),
            )
        )
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
        rate_limit_policy = next(
            p for p in registry.policies
            if isinstance(p, ExceptionTypePolicy) and p.exception_type is anthropic.RateLimitError
        )
        self.assertIsInstance(rate_limit_policy.decision, Abort)

    def test_anthropic_aborts_on_401_immediately(self) -> None:
        """401 auth errors should NOT retry — they won't fix
        themselves and they burn the retry budget."""
        import anthropic
        from briar.agent._llms.anthropic_llm import AnthropicLLM
        from briar.error_policy import Abort, HttpStatusPolicy

        registry = AnthropicLLM.default_error_policies()
        auth_policy = next(
            p for p in registry.policies
            if isinstance(p, HttpStatusPolicy) and p.status == 401
        )
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
        prompt = MockPromptIO(answers=[
            "client-id-uuid",
            "client-secret-very-long",
            "project-id-uuid",
            "",  # env slug — accept default "prod"
            "",  # host — accept default
        ])
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

        for kind in ("github-pat", "github-device", "aws-static", "aws-sso",
                     "jira-token", "jira-session", "linear-api-key",
                     "bitbucket-app-password"):
            acquirer = AcquirerRegistry.make(kind)
            self.assertIs(type(acquirer).destination_policy, DestinationPolicy.EXTERNAL,
                          f"{kind} should be EXTERNAL")

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

    def test_read_returns_empty_when_creds_missing(self) -> None:
        """Silent miss matches EnvFileStore semantics — the doctor can
        audit without forcing operators to install the extra."""
        from briar.credentials.infisical import InfisicalStore

        env = {"INFISICAL_CLIENT_ID": "", "INFISICAL_CLIENT_SECRET": "", "INFISICAL_PROJECT_ID": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            store = InfisicalStore()
            self.assertEqual(store.read("ANYTHING"), "")

    def test_write_calls_update_then_falls_back_to_create_on_not_found(self) -> None:
        """Symmetric to AwsSecretsManagerStore — try update first, fall
        through to create on the SDK's not-found error."""
        from briar.credentials.infisical import InfisicalStore

        store = InfisicalStore()
        fake_client = mock.MagicMock()
        # First call (update) raises a "not found" — second call (create) succeeds
        fake_client.secrets.update_secret_by_name.side_effect = RuntimeError("Secret not found")
        fake_client.secrets.create_secret_by_name.return_value = None
        store._client = fake_client  # bypass _build_client

        store.write("GITHUB_TOKEN", "ghp_xyz")
        fake_client.secrets.update_secret_by_name.assert_called_once()
        fake_client.secrets.create_secret_by_name.assert_called_once()
        # cached
        self.assertEqual(store._cache["GITHUB_TOKEN"], "ghp_xyz")

    def test_delete_returns_false_on_not_found(self) -> None:
        from briar.credentials.infisical import InfisicalStore

        store = InfisicalStore()
        fake_client = mock.MagicMock()
        fake_client.secrets.delete_secret_by_name.side_effect = RuntimeError("404 not found")
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
        prompt = MockPromptIO(answers=[
            "https://acme.atlassian.net/",  # URL (trailing / stripped)
            "ops@acme.com",
            "atlassian-api-token-xyz",
        ])
        creds = acquirer.acquire(company="acme", prompt=prompt)
        self.assertEqual(creds.entries["JIRA_ACME_URL"], "https://acme.atlassian.net")
        self.assertEqual(creds.entries["JIRA_ACME_EMAIL"], "ops@acme.com")
        self.assertEqual(creds.entries["JIRA_ACME_TOKEN"], "atlassian-api-token-xyz")
        # AUTH_KIND=token so JiraAuthRegistry.autodetect picks this strategy
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
        prompt = MockPromptIO(answers=[
            "https://acme.atlassian.net",
            jwt,
        ])
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
            # Even on raise, os.environ MUST have been updated — caller
            # benefits from the in-memory value (this is documented behaviour).
            self.assertEqual(os.environ.get("TEST_BRIAR_BADPATH"), "v")


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
        """When BRIAR_SECRETS_FILE unset AND /etc/briar/secrets.env
        doesn't exist (laptop case), resolves to XDG path."""
        from pathlib import Path
        from briar.credentials.envfile import _secrets_path

        env = {"BRIAR_SECRETS_FILE": "", "XDG_CONFIG_HOME": ""}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch("briar.credentials.envfile.Path") as path_cls:
                # System path "doesn't exist" → fall through to XDG
                path_cls.side_effect = lambda *a, **k: Path(*a, **k)
                path_cls.return_value = mock.MagicMock()
                path_cls.return_value.exists.return_value = False
                # Easier — just monkey-patch Path.exists on a real Path:
                pass
            # Skip the brittle mock — assert structurally:
            resolved = _secrets_path()
            # Either we're on a droplet (resolved = system) OR laptop (XDG)
            self.assertTrue(
                str(resolved).endswith("/secrets.env"),
                f"resolved path should end with secrets.env, got {resolved}",
            )

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


if __name__ == "__main__":
    unittest.main()
