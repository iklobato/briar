"""Slack + Telegram NotificationSinks (vs the Slack/Telegram message
writers, which test_slack_channel.py / test_telegram_chat.py cover)."""

from __future__ import annotations

import json

import pytest

from briar.notify.slack import SlackSink
from briar.notify.telegram import TelegramSink


def _mock_urlopen(mocker, body: str | bytes):
    captured = []

    class _Resp:
        def read(self):
            return body.encode("utf-8") if isinstance(body, str) else body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def _urlopen(req, *args, **kwargs):
        captured.append({"url": req.full_url, "data": req.data})
        return _Resp()

    mocker.patch("urllib.request.urlopen", side_effect=_urlopen)
    return captured


class TestSlackSink:
    def test_unavailable_without_webhook(self) -> None:
        assert SlackSink(company="acme").is_available() is False

    def test_send_skipped_when_unavailable(self, caplog_briar) -> None:
        assert SlackSink(company="acme").send(title="t", body="b") is False
        assert any("no webhook" in r.message for r in caplog_briar.records)

    def test_send_ok_response_returns_true(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("SLACK_ACME_WEBHOOK_URL", "https://hooks.slack.com/xxx")
        _mock_urlopen(mocker, "ok")
        assert SlackSink(company="acme").send(title="t", body="b") is True

    def test_send_non_ok_response_returns_false(self, monkeypatch, mocker, caplog_briar) -> None:
        monkeypatch.setenv("SLACK_ACME_WEBHOOK_URL", "https://hooks.slack.com/xxx")
        _mock_urlopen(mocker, "invalid_payload")
        assert SlackSink(company="acme").send(title="t", body="b") is False

    def test_payload_has_bold_title(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("SLACK_ACME_WEBHOOK_URL", "https://hooks.slack.com/xxx")
        captured = _mock_urlopen(mocker, "ok")
        SlackSink(company="acme").send(title="Alert", body="body")
        payload = json.loads(captured[0]["data"])
        assert "*Alert*" in payload["text"]


class TestTelegramSink:
    def test_unavailable_without_token_or_chat(self) -> None:
        assert TelegramSink(company="acme").is_available() is False

    def test_available_with_both(self, monkeypatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT")
        monkeypatch.setenv("TELEGRAM_ACME_CHAT_ID", "12345")
        assert TelegramSink(company="acme").is_available() is True

    def test_send_ok_true_returns_true(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT")
        monkeypatch.setenv("TELEGRAM_ACME_CHAT_ID", "12345")
        _mock_urlopen(mocker, json.dumps({"ok": True}))
        assert TelegramSink(company="acme").send(title="t", body="b") is True

    def test_send_ok_false_returns_false(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT")
        monkeypatch.setenv("TELEGRAM_ACME_CHAT_ID", "12345")
        _mock_urlopen(mocker, json.dumps({"ok": False, "description": "chat not found"}))
        assert TelegramSink(company="acme").send(title="t", body="b") is False
