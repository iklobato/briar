"""Smoke tests for the new abstraction layers (TrackerProvider,
LLMProvider, CloudProvider, NotificationSink, CredentialStore).

These tests focus on registry shape, factory error paths, and the
contract surface — not adapter behaviour (which needs network +
credentials). Each adapter family is verified to register at least
one real implementation and one or more stubs that fail loudly via
``NotImplementedError`` rather than silently."""

from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
