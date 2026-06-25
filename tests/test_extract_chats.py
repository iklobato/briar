"""Tests for the ChatProvider abstraction + SlackChatProvider adapter +
slack-context extractor + agent CLI wiring.

Mirrors tests/test_extract_meetings.py — registry shape, factory error
paths, Slack response translation under a urlopen mock, the read-only
guard, extractor section shape, and the new --slack-* flags on
ImplementOp / PrfixOp."""

from __future__ import annotations

import argparse
import json
import unittest
from unittest import mock

# ─── ChatProvider registry + factory ────────────────────────────────────────


class ChatRegistryTests(unittest.TestCase):
    def test_slack_kind_registered(self) -> None:
        from briar.extract._chats import chat_kinds

        self.assertIn("slack", chat_kinds())

    def test_unknown_kind_raises(self) -> None:
        from briar.errors import CliError
        from briar.extract._chats import make_chat

        with self.assertRaises(CliError):
            make_chat("discord", company="acme")

    def test_slack_unavailable_without_creds(self) -> None:
        from briar.extract._chats import make_chat

        with mock.patch.dict("os.environ", {}, clear=True):
            provider = make_chat("slack", company="acme")
            self.assertFalse(provider.is_available())

    def test_slack_unavailable_with_token_but_no_cookie(self) -> None:
        from briar.extract._chats import make_chat

        with mock.patch.dict("os.environ", {"SLACK_ACME_TOKEN": "xoxc-1"}, clear=True):
            provider = make_chat("slack", company="acme")
            self.assertFalse(provider.is_available())

    def test_slack_required_env_vars(self) -> None:
        from briar.extract._chats.slack import SlackChatProvider

        required = SlackChatProvider.required_env_vars(company="acme")
        self.assertEqual(required, ["SLACK_ACME_TOKEN", "SLACK_ACME_COOKIE_D"])
        self.assertEqual(SlackChatProvider.required_env_vars(company=""), [])


# ─── SlackChatProvider translation + read-only guard ───────────────────────


class _Creds:
    env = {"SLACK_ACME_TOKEN": "xoxc-1", "SLACK_ACME_COOKIE_D": "xoxd-1"}


class SlackAdapterTests(unittest.TestCase):
    def _mock_urlopen(self, payload: dict) -> mock.MagicMock:
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps(payload).encode("utf-8")
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    def test_search_messages_translates_response(self) -> None:
        from briar.extract._chats import make_chat

        payload = {
            "ok": True,
            "messages": {
                "matches": [
                    {
                        "ts": "1716422400.001",
                        "text": "ACME-123 oauth rollout is blocked on the migration",
                        "username": "alice",
                        "channel": {"id": "C01", "name": "eng"},
                        "permalink": "https://acme.slack.com/archives/C01/p1716422400001",
                    }
                ]
            },
        }
        with mock.patch.dict("os.environ", _Creds.env, clear=True):
            provider = make_chat("slack", company="acme")
            self.assertTrue(provider.is_available())
            with mock.patch("urllib.request.urlopen", return_value=self._mock_urlopen(payload)):
                hits = provider.search_messages(query="ACME-123", max_count=5)
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.channel_id, "C01")
        self.assertEqual(hit.channel_name, "eng")
        self.assertEqual(hit.ts, "1716422400.001")
        self.assertIn("ACME-123", hit.text)
        self.assertTrue(hit.permalink.startswith("https://"))

    def test_search_sends_token_in_body_and_cookie_header(self) -> None:
        from briar.extract._chats import make_chat

        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = req.data.decode("utf-8")
            captured["cookie"] = req.headers.get("Cookie")
            captured["url"] = req.full_url
            return self._mock_urlopen({"ok": True, "messages": {"matches": []}})

        with mock.patch.dict("os.environ", _Creds.env, clear=True):
            provider = make_chat("slack", company="acme")
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                provider.search_messages(query="deploy failed", max_count=3)
        self.assertIn("token=xoxc-1", captured["body"])
        self.assertIn("deploy+failed", captured["body"])
        self.assertEqual(captured["cookie"], "d=xoxd-1")
        self.assertTrue(captured["url"].endswith("/api/search.messages"))

    def test_get_thread_translates_replies(self) -> None:
        from briar.extract._chats import make_chat

        payload = {
            "ok": True,
            "messages": [
                {"ts": "1716422400.001", "user": "alice", "text": "We should use refresh tokens."},
                {"ts": "1716422460.002", "user": "bob", "text": "Agreed, ship it."},
                {"ts": "1716422500.003", "user": "carol", "text": ""},  # empty → dropped
            ],
        }
        with mock.patch.dict("os.environ", _Creds.env, clear=True):
            provider = make_chat("slack", company="acme")
            with mock.patch("urllib.request.urlopen", return_value=self._mock_urlopen(payload)):
                thread = provider.get_thread(channel_id="C01", thread_ts="1716422400.001", max_count=50)
        self.assertEqual(thread.channel_id, "C01")
        self.assertEqual(len(thread.messages), 2)
        self.assertEqual(thread.messages[0].author, "alice")
        self.assertIn("refresh tokens", thread.messages[0].text)

    def test_api_error_swallowed_to_default(self) -> None:
        from briar.extract._chats import make_chat

        with mock.patch.dict("os.environ", _Creds.env, clear=True):
            provider = make_chat("slack", company="acme")
            with mock.patch("urllib.request.urlopen", return_value=self._mock_urlopen({"ok": False, "error": "not_authed"})):
                # @swallow_errors turns the raised RuntimeError into the
                # declared default ([] for search) so a bad session token
                # degrades to "no context", never crashes the agent run.
                hits = provider.search_messages(query="x", max_count=3)
        self.assertEqual(hits, [])

    def test_auth_error_message_names_the_fix(self) -> None:
        # An expired session token/cookie is the dominant failure mode for
        # web-session auth; the error must tell the operator to refresh
        # (mirrors the slack skill's hint), so the swallowed log line is
        # actionable rather than a bare `invalid_auth`.
        from briar.extract._chats.slack import SlackChatProvider

        with mock.patch.dict("os.environ", _Creds.env, clear=True):
            provider = SlackChatProvider(company="acme")
            with mock.patch("urllib.request.urlopen", return_value=self._mock_urlopen({"ok": False, "error": "invalid_auth"})):
                with self.assertRaises(RuntimeError) as ctx:
                    provider._call("search.messages", {"query": "x"})
        message = str(ctx.exception)
        self.assertIn("invalid_auth", message)
        self.assertIn("expired", message)
        self.assertIn("refresh", message)

    def test_non_auth_error_has_no_refresh_hint(self) -> None:
        # A non-auth error (e.g. a transient/server error name) must not
        # get the credentials-refresh hint, which would mislead.
        from briar.extract._chats.slack import SlackChatProvider

        with mock.patch.dict("os.environ", _Creds.env, clear=True):
            provider = SlackChatProvider(company="acme")
            with mock.patch("urllib.request.urlopen", return_value=self._mock_urlopen({"ok": False, "error": "ratelimited"})):
                with self.assertRaises(RuntimeError) as ctx:
                    provider._call("search.messages", {"query": "x"})
        self.assertNotIn("refresh", str(ctx.exception))

    def test_read_only_guard_refuses_write_method(self) -> None:
        from briar.extract._chats.slack import NotReadOnly, SlackChatProvider

        provider = SlackChatProvider(company="acme")
        with self.assertRaises(NotReadOnly):
            provider._call("chat.postMessage", {"channel": "C01", "text": "hi"})


# ─── slack-context (task-scoped) extractor ──────────────────────────────────


class SlackContextExtractorTests(unittest.TestCase):
    def _ns(self, **over) -> argparse.Namespace:
        base = dict(company="acme", chat="slack", slack_query="", slack_top_k=3, slack_max_bytes=30_000)
        base.update(over)
        return argparse.Namespace(**base)

    def test_empty_when_no_query(self) -> None:
        from briar.extract.slack_context import FetchSlackContext

        ext = FetchSlackContext()
        with mock.patch.object(ext, "_chat") as make_provider:
            provider = mock.MagicMock()
            provider.is_available.return_value = True
            make_provider.return_value = provider
            section = ext.fetch(self._ns(slack_query=""))
        self.assertTrue(section.is_empty)

    def test_empty_when_provider_unavailable(self) -> None:
        from briar.extract.slack_context import FetchSlackContext

        ext = FetchSlackContext()
        with mock.patch.object(ext, "_chat") as make_provider:
            provider = mock.MagicMock()
            provider.is_available.return_value = False
            make_provider.return_value = provider
            section = ext.fetch(self._ns(slack_query="ACME-123"))
        self.assertTrue(section.is_empty)
        provider.search_messages.assert_not_called()

    def test_fetch_by_query_renders_threads(self) -> None:
        from briar.extract._chat import ChatHit, ChatMessage, ChatThread
        from briar.extract.slack_context import FetchSlackContext

        ext = FetchSlackContext()
        hits = [
            ChatHit(channel_id="C01", channel_name="eng", ts="1716422400.001", text="ACME-123?", permalink="https://acme.slack.com/p1"),
            ChatHit(channel_id="C02", channel_name="product", ts="1716422500.002", text="re ACME-123", permalink="https://acme.slack.com/p2"),
        ]
        threads = {
            "1716422400.001": ChatThread(
                channel_id="C01",
                channel_name="",
                root_ts="1716422400.001",
                messages=[ChatMessage(ts="1716422400.001", author="alice", text="ACME-123 should ship Friday.")],
            ),
            "1716422500.002": ChatThread(
                channel_id="C02",
                channel_name="",
                root_ts="1716422500.002",
                messages=[ChatMessage(ts="1716422500.002", author="bob", text="Confirmed for ACME-123.")],
            ),
        }
        with mock.patch.object(ext, "_chat") as make_provider:
            provider = mock.MagicMock()
            provider.is_available.return_value = True
            provider.search_messages.return_value = hits
            provider.get_thread.side_effect = lambda *, channel_id, thread_ts, max_count: threads[thread_ts]
            make_provider.return_value = provider
            section = ext.fetch(self._ns(slack_query="ACME-123", slack_top_k=2))
        self.assertFalse(section.is_empty)
        self.assertIn("2 thread", section.title)
        self.assertIn("ACME-123 should ship", section.body)
        self.assertIn("Confirmed for ACME-123", section.body)
        # Channel name is grafted from the search hit onto the thread render.
        self.assertIn("#eng", section.body)
        self.assertEqual(section.data["match_count"], 2)

    def test_thread_truncated_at_max_bytes(self) -> None:
        from briar.extract._chat import ChatHit, ChatMessage, ChatThread
        from briar.extract.slack_context import FetchSlackContext

        ext = FetchSlackContext()
        hit = ChatHit(channel_id="C01", channel_name="eng", ts="1716422400.001", text="big")
        long_thread = ChatThread(
            channel_id="C01",
            channel_name="",
            root_ts="1716422400.001",
            messages=[ChatMessage(ts="1716422400.001", author="alice", text="blah " * 4000)],
        )
        with mock.patch.object(ext, "_chat") as make_provider:
            provider = mock.MagicMock()
            provider.is_available.return_value = True
            provider.search_messages.return_value = [hit]
            provider.get_thread.return_value = long_thread
            make_provider.return_value = provider
            section = ext.fetch(self._ns(slack_query="ACME-123", slack_max_bytes=1024))
        self.assertIn("thread truncated", section.body)
        self.assertLess(len(section.body.encode("utf-8")), 5_000)


# ─── Agent CLI wiring (--slack-* flags + helper) ───────────────────────────


class AgentSlackWiringTests(unittest.TestCase):
    def test_implement_op_registers_slack_flags(self) -> None:
        from briar.commands.agent import ImplementOp

        parser = argparse.ArgumentParser()
        ImplementOp().add_arguments(parser)
        flags = {a.dest for a in parser._actions}
        for expected in ("chat", "slack_query", "slack_top_k", "slack_max_bytes"):
            self.assertIn(expected, flags)

    def test_prfix_op_registers_slack_flags(self) -> None:
        from briar.commands.agent import PrfixOp

        parser = argparse.ArgumentParser()
        PrfixOp().add_arguments(parser)
        flags = {a.dest for a in parser._actions}
        for expected in ("chat", "slack_query", "slack_top_k", "slack_max_bytes"):
            self.assertIn(expected, flags)

    def test_fetch_slack_context_empty_when_no_query(self) -> None:
        from briar.commands.agent import CommandAgent

        result = CommandAgent._fetch_slack_context(
            company="acme",
            chat_kind="slack",
            slack_query="",
            slack_top_k=3,
            slack_max_bytes=30_000,
        )
        self.assertEqual(result, [])

    def test_fetch_slack_context_returns_section_on_match(self) -> None:
        from briar.commands.agent import CommandAgent
        from briar.extract.base import ExtractedSection

        fake_section = ExtractedSection(title="Slack context — match", body="...")
        mock_extractor = mock.MagicMock()
        mock_extractor.fetch.return_value = fake_section
        with mock.patch.dict("briar.extract.TASK_SCOPED_EXTRACTORS", {"slack-context": mock_extractor}):
            result = CommandAgent._fetch_slack_context(
                company="acme",
                chat_kind="slack",
                slack_query="ACME-123",
                slack_top_k=3,
                slack_max_bytes=30_000,
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "Slack context — match")


if __name__ == "__main__":
    unittest.main()
