"""TelegramChatWriter — Bot API sendMessage."""

from __future__ import annotations

import json
import urllib.parse

import pytest

from briar.messaging.telegram_chat import TelegramChatWriter


def _mock_urlopen(mocker, response: dict):
    captured = []

    class _Resp:
        def read(self):
            return json.dumps(response).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def _urlopen(req, *args, **kwargs):
        captured.append({"url": req.full_url, "data": req.data})
        return _Resp()

    mocker.patch("urllib.request.urlopen", side_effect=_urlopen)
    return captured


class TestAvailability:
    def test_missing_both_token_and_chat_id(self) -> None:
        assert TelegramChatWriter(company="acme").is_available() is False

    def test_token_only_not_enough(self, monkeypatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT")
        assert TelegramChatWriter(company="acme").is_available() is False

    def test_token_and_chat_id_available(self, monkeypatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT")
        monkeypatch.setenv("TELEGRAM_ACME_CHAT_ID", "12345")
        assert TelegramChatWriter(company="acme").is_available() is True

    def test_chat_env_binding_override(self, monkeypatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT")
        monkeypatch.setenv("CUSTOM_OPS_CHAT", "99999")
        w = TelegramChatWriter(company="acme", config={"chat_env": "CUSTOM_OPS_CHAT"})
        assert w.is_available() is True


class TestSend:
    def test_target_overrides_binding_chat_id(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT")
        monkeypatch.setenv("TELEGRAM_ACME_CHAT_ID", "default-12345")
        w = TelegramChatWriter(company="acme")
        captured = _mock_urlopen(mocker, {"ok": True, "result": {"message_id": 42}})
        w.send(target="override-99", body="hello")
        # Decode form payload
        parsed = urllib.parse.parse_qs(captured[0]["data"].decode())
        assert parsed["chat_id"] == ["override-99"]

    def test_send_ok_returns_result_with_message_id_ref(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT")
        monkeypatch.setenv("TELEGRAM_ACME_CHAT_ID", "12345")
        w = TelegramChatWriter(company="acme")
        _mock_urlopen(mocker, {"ok": True, "result": {"message_id": 42}})
        result = w.send(target="", body="hello")
        assert result.ok is True
        assert result.ref == "42"

    def test_send_api_returns_ok_false_returns_failure(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "BOT")
        monkeypatch.setenv("TELEGRAM_ACME_CHAT_ID", "12345")
        w = TelegramChatWriter(company="acme")
        _mock_urlopen(mocker, {"ok": False, "description": "chat not found"})
        result = w.send(target="", body="hello")
        assert result.ok is False
        assert "chat not found" in result.detail

    def test_missing_token_returns_failure(self, monkeypatch) -> None:
        # No TELEGRAM_BOT_TOKEN; chat_id present (won't matter since token missing).
        monkeypatch.setenv("TELEGRAM_ACME_CHAT_ID", "12345")
        w = TelegramChatWriter(company="acme")
        result = w.send(target="", body="hello")
        assert result.ok is False
        assert "missing" in result.detail.lower()


class TestRequiredEnvVars:
    def test_includes_token_and_per_company_chat_id(self) -> None:
        out = TelegramChatWriter.required_env_vars(company="acme")
        assert "TELEGRAM_BOT_TOKEN" in out
        assert "TELEGRAM_ACME_CHAT_ID" in out
