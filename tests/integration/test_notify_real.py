"""End-to-end: the REAL notification sinks (stdlib urllib) POSTing to a
wire-level mock, so the env-driven URL build, the JSON payload serialization,
the real urllib send, and the bool return mapping all run.

Slack incoming webhook:
  POST <webhook-url>  body {"text": "..."}  -> 200 with the literal body "ok"
  https://api.slack.com/messaging/webhooks#handling_errors

Telegram Bot API sendMessage:
  POST https://api.telegram.org/bot<token>/sendMessage  (form-encoded)
  -> 200 {"ok": true, "result": {...}}; on error {"ok": false, "description": ...}
  https://core.telegram.org/bots/api#making-requests

PagerDuty Events API v2:
  POST https://events.pagerduty.com/v2/enqueue  body {"routing_key", "event_action", ...}
  -> 202 {"status": "success", "message": "...", "dedup_key": "..."}
  https://developer.pagerduty.com/docs/send-an-event-events-api-v2
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


# --- Slack -----------------------------------------------------------------


def test_slack_real_posts_text_and_returns_true(mock_api, monkeypatch) -> None:
    from briar.notify.slack import SlackSink

    # Slack returns the literal text "ok" (not JSON) on a 200.
    mock_api.add("POST", "/webhook", "ok", raw=True)
    monkeypatch.setenv("SLACK_ACME_WEBHOOK_URL", mock_api.base_url + "/webhook")

    assert SlackSink(company="acme").send(title="Build failed", body="see logs") is True

    posts = [r for r in mock_api.received if r["method"] == "POST" and r["path"] == "/webhook"]
    assert posts, f"slack never posted; received={[(r['method'], r['path']) for r in mock_api.received]}"
    payload = json.loads(posts[0]["body"])
    # title is bolded, body on the next line.
    assert payload == {"text": "*Build failed*\nsee logs"}


def test_slack_real_non_ok_body_returns_false(mock_api, monkeypatch) -> None:
    """Slack returns a non-"ok" body (e.g. "invalid_payload") on a problem;
    the sink reports False without raising."""
    from briar.notify.slack import SlackSink

    mock_api.add("POST", "/webhook", "invalid_payload", raw=True)
    monkeypatch.setenv("SLACK_ACME_WEBHOOK_URL", mock_api.base_url + "/webhook")

    assert SlackSink(company="acme").send(title="x", body="y") is False
    assert [r for r in mock_api.received if r["method"] == "POST"]  # it really tried


def test_slack_real_404_is_swallowed(mock_api, monkeypatch) -> None:
    """A revoked webhook -> 404; the HTTPError is swallowed to False."""
    from briar.notify.slack import SlackSink

    mock_api.add("POST", "/webhook", "no_service", status=404, raw=True)
    monkeypatch.setenv("SLACK_ACME_WEBHOOK_URL", mock_api.base_url + "/webhook")

    assert SlackSink(company="acme").send(title="x", body="y") is False


# --- Telegram --------------------------------------------------------------


def test_telegram_real_posts_form_and_returns_true(mock_api, monkeypatch) -> None:
    from briar.notify import telegram as tg_mod
    from briar.notify.telegram import TelegramSink

    monkeypatch.setattr(tg_mod, "_API_BASE", mock_api.base_url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TELEGRAM-BOT-TOKEN-PLACEHOLDER-not-a-secret")
    monkeypatch.setenv("TELEGRAM_ACME_CHAT_ID", "-1001234567890")
    # Documented success envelope.
    mock_api.add("POST", "/bot", {"ok": True, "result": {"message_id": 7, "chat": {"id": -1001234567890}}})

    assert TelegramSink(company="acme").send(title="Deploy", body="all green") is True

    posts = [r for r in mock_api.received if r["method"] == "POST" and "/sendMessage" in r["path"]]
    assert posts, f"telegram never posted; received={[(r['method'], r['path']) for r in mock_api.received]}"
    # Token lands in the path; payload is form-encoded.
    assert "TELEGRAM-BOT-TOKEN-PLACEHOLDER-not-a-secret" in posts[0]["path"]
    from urllib.parse import parse_qs

    form = parse_qs(posts[0]["body"].decode())
    assert form["chat_id"] == ["-1001234567890"]
    assert form["text"] == ["*Deploy*\n\nall green"]
    assert form["parse_mode"] == ["Markdown"]


def test_telegram_real_not_ok_returns_false(mock_api, monkeypatch) -> None:
    """Telegram returns {"ok": false, ...} (e.g. bad chat id) -> False."""
    from briar.notify import telegram as tg_mod
    from briar.notify.telegram import TelegramSink

    monkeypatch.setattr(tg_mod, "_API_BASE", mock_api.base_url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TELEGRAM-BOT-TOKEN-PLACEHOLDER-not-a-secret")
    monkeypatch.setenv("TELEGRAM_ACME_CHAT_ID", "-1001234567890")
    mock_api.add("POST", "/bot", {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})

    assert TelegramSink(company="acme").send(title="x", body="y") is False


# --- PagerDuty -------------------------------------------------------------


def test_pagerduty_real_posts_event_and_returns_true(mock_api, monkeypatch) -> None:
    from briar.notify import pagerduty as pd_mod
    from briar.notify.pagerduty import PagerDutySink

    monkeypatch.setattr(pd_mod, "_ENDPOINT", mock_api.base_url + "/v2/enqueue")
    monkeypatch.setenv("PAGERDUTY_ACME_ROUTING_KEY", "PAGERDUTY-ROUTING-KEY-PLACEHOLDER-not-a-secret")
    # Documented Events API v2 success envelope (HTTP 202).
    mock_api.add("POST", "/v2/enqueue", {"status": "success", "message": "Event processed", "dedup_key": "abc123"}, status=202)

    assert PagerDutySink(company="acme").send(title="DB down", body="primary unreachable") is True

    posts = [r for r in mock_api.received if r["method"] == "POST" and r["path"] == "/v2/enqueue"]
    assert posts, f"pagerduty never posted; received={[(r['method'], r['path']) for r in mock_api.received]}"
    payload = json.loads(posts[0]["body"])
    assert payload["routing_key"] == "PAGERDUTY-ROUTING-KEY-PLACEHOLDER-not-a-secret"
    assert payload["event_action"] == "trigger"
    assert payload["payload"]["summary"] == "DB down"
    assert payload["payload"]["severity"] == "warning"
    assert payload["payload"]["source"] == "briar:acme"
    assert payload["payload"]["custom_details"] == {"body": "primary unreachable"}
    # dedup_key is the 16-char title hash (stable trigger dedup).
    assert isinstance(payload["dedup_key"], str) and len(payload["dedup_key"]) == 16


def test_pagerduty_real_non_success_returns_false(mock_api, monkeypatch) -> None:
    """Events API returns status != "success" (e.g. an invalid routing key
    envelope) -> False, without raising."""
    from briar.notify import pagerduty as pd_mod
    from briar.notify.pagerduty import PagerDutySink

    monkeypatch.setattr(pd_mod, "_ENDPOINT", mock_api.base_url + "/v2/enqueue")
    monkeypatch.setenv("PAGERDUTY_ACME_ROUTING_KEY", "PAGERDUTY-ROUTING-KEY-PLACEHOLDER-not-a-secret")
    mock_api.add(
        "POST",
        "/v2/enqueue",
        {"status": "invalid event", "message": "Event object is invalid", "errors": ["routing_key not found"]},
        status=400,
    )

    assert PagerDutySink(company="acme").send(title="x", body="y") is False


def test_pagerduty_real_dedup_key_is_title_stable(mock_api, monkeypatch) -> None:
    """Same company+title -> same dedup_key across calls (so a repeating
    failure collapses into one incident rather than N)."""
    from briar.notify import pagerduty as pd_mod
    from briar.notify.pagerduty import PagerDutySink

    monkeypatch.setattr(pd_mod, "_ENDPOINT", mock_api.base_url + "/v2/enqueue")
    monkeypatch.setenv("PAGERDUTY_ACME_ROUTING_KEY", "PAGERDUTY-ROUTING-KEY-PLACEHOLDER-not-a-secret")
    mock_api.add("POST", "/v2/enqueue", {"status": "success", "dedup_key": "x"}, status=202)
    mock_api.add("POST", "/v2/enqueue", {"status": "success", "dedup_key": "x"}, status=202)

    sink = PagerDutySink(company="acme")
    assert sink.send(title="DB down", body="a") is True
    assert sink.send(title="DB down", body="b") is True

    posts = [json.loads(r["body"]) for r in mock_api.received if r["method"] == "POST"]
    assert len(posts) == 2
    assert posts[0]["dedup_key"] == posts[1]["dedup_key"]
