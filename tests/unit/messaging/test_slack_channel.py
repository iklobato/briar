"""SlackChannelWriter — webhook POST, response must be literal `ok`."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager

import pytest

from briar.messaging.slack_channel import SlackChannelWriter


@contextmanager
def _mock_urlopen(mocker, response_body: str):
    """Mock urllib.request.urlopen returning a context-manager that reads
    `response_body`. Records the actual request via `captured`."""
    captured = []

    class _Resp:
        def read(self):
            return response_body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def _urlopen(req, *args, **kwargs):
        captured.append({"url": req.full_url, "data": req.data, "headers": dict(req.headers)})
        return _Resp()

    mocker.patch("urllib.request.urlopen", side_effect=_urlopen)
    yield captured


class TestSlackChannel:
    def test_is_unavailable_without_webhook(self) -> None:
        w = SlackChannelWriter(company="acme")
        assert w.is_available() is False

    def test_is_available_when_company_webhook_env_set(self, monkeypatch) -> None:
        monkeypatch.setenv("SLACK_ACME_WEBHOOK_URL", "https://hooks.slack.com/xxx")
        w = SlackChannelWriter(company="acme")
        assert w.is_available() is True

    def test_binding_override_webhook_env(self, monkeypatch) -> None:
        monkeypatch.setenv("CUSTOM_WEBHOOK", "https://hooks.slack.com/custom")
        w = SlackChannelWriter(company="acme", config={"webhook_env": "CUSTOM_WEBHOOK"})
        assert w.is_available() is True

    def test_send_no_webhook_returns_ok_false(self) -> None:
        w = SlackChannelWriter(company="acme")
        result = w.send(target="", body="hello")
        assert result.ok is False
        assert "missing" in result.detail.lower()

    def test_send_response_ok_returns_ok_true(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("SLACK_ACME_WEBHOOK_URL", "https://hooks.slack.com/xxx")
        w = SlackChannelWriter(company="acme")
        with _mock_urlopen(mocker, "ok") as captured:
            result = w.send(target="", body="hello")
        assert result.ok is True
        # Verify request shape
        assert captured[0]["url"] == "https://hooks.slack.com/xxx"
        assert captured[0]["headers"].get("Content-type") == "application/json"

    def test_send_response_not_ok_returns_failure_with_body(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("SLACK_ACME_WEBHOOK_URL", "https://hooks.slack.com/xxx")
        w = SlackChannelWriter(company="acme")
        with _mock_urlopen(mocker, "invalid_payload") as _:
            result = w.send(target="", body="hello")
        assert result.ok is False
        assert "invalid_payload" in result.detail

    def test_send_with_title_prepends_bold(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("SLACK_ACME_WEBHOOK_URL", "https://hooks.slack.com/xxx")
        w = SlackChannelWriter(company="acme")
        with _mock_urlopen(mocker, "ok") as captured:
            w.send(target="", body="body-text", title="Alert!")
        payload = json.loads(captured[0]["data"])
        assert "*Alert!*" in payload["text"]
        assert "body-text" in payload["text"]

    def test_send_swallows_exception_returns_failure(self, monkeypatch, mocker, caplog_briar) -> None:
        monkeypatch.setenv("SLACK_ACME_WEBHOOK_URL", "https://hooks.slack.com/xxx")
        w = SlackChannelWriter(company="acme")
        mocker.patch("urllib.request.urlopen", side_effect=RuntimeError("network down"))
        result = w.send(target="", body="hello")
        # @swallow_errors converts to SendResult(ok=False, detail="exception")
        assert result.ok is False

    def test_required_env_vars_lists_company_webhook(self) -> None:
        names = SlackChannelWriter.required_env_vars(company="acme")
        assert "SLACK_ACME_WEBHOOK_URL" in names

    def test_required_env_vars_empty_without_company(self) -> None:
        assert SlackChannelWriter.required_env_vars(company="") == []
