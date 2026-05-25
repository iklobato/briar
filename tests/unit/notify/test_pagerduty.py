"""PagerDuty sink — Events API v2."""

from __future__ import annotations

import hashlib
import json

import pytest

from briar.notify.pagerduty import PagerDutySink


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
        captured.append({"url": req.full_url, "data": req.data, "headers": dict(req.headers)})
        return _Resp()

    mocker.patch("urllib.request.urlopen", side_effect=_urlopen)
    return captured


class TestAvailability:
    def test_no_routing_key_unavailable(self) -> None:
        assert PagerDutySink(company="acme").is_available() is False

    def test_with_routing_key_available(self, monkeypatch) -> None:
        monkeypatch.setenv("PAGERDUTY_ACME_ROUTING_KEY", "rk-123")
        assert PagerDutySink(company="acme").is_available() is True

    def test_empty_company_no_routing_key(self) -> None:
        # No company → no env-var lookup at all
        assert PagerDutySink(company="").is_available() is False


class TestSend:
    def test_send_skipped_without_routing_key_returns_false(self, caplog_briar) -> None:
        sink = PagerDutySink(company="acme")
        assert sink.send(title="t", body="b") is False
        assert any("no routing key" in r.message for r in caplog_briar.records)

    def test_send_success_returns_true(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("PAGERDUTY_ACME_ROUTING_KEY", "rk-123")
        sink = PagerDutySink(company="acme")
        _mock_urlopen(mocker, {"status": "success"})
        assert sink.send(title="t", body="b") is True

    def test_send_non_success_returns_false(self, monkeypatch, mocker, caplog_briar) -> None:
        monkeypatch.setenv("PAGERDUTY_ACME_ROUTING_KEY", "rk-123")
        sink = PagerDutySink(company="acme")
        _mock_urlopen(mocker, {"status": "invalid", "error": "bad-key"})
        assert sink.send(title="t", body="b") is False

    def test_dedup_key_deterministic_per_company_title(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("PAGERDUTY_ACME_ROUTING_KEY", "rk-123")
        sink = PagerDutySink(company="acme")
        captured = _mock_urlopen(mocker, {"status": "success"})
        sink.send(title="Alert", body="b1")
        sink.send(title="Alert", body="b2")
        sink.send(title="Different", body="b1")
        keys = [json.loads(c["data"])["dedup_key"] for c in captured]
        # Same title → same dedup_key; different title → different key
        assert keys[0] == keys[1]
        assert keys[0] != keys[2]

    def test_dedup_key_length_16_hex(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("PAGERDUTY_ACME_ROUTING_KEY", "rk-123")
        sink = PagerDutySink(company="acme")
        captured = _mock_urlopen(mocker, {"status": "success"})
        sink.send(title="t", body="b")
        dedup = json.loads(captured[0]["data"])["dedup_key"]
        assert len(dedup) == 16
        # All hex chars
        int(dedup, 16)

    def test_summary_truncated_at_1024(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("PAGERDUTY_ACME_ROUTING_KEY", "rk-123")
        sink = PagerDutySink(company="acme")
        captured = _mock_urlopen(mocker, {"status": "success"})
        sink.send(title="x" * 2000, body="b")
        summary = json.loads(captured[0]["data"])["payload"]["summary"]
        assert len(summary) == 1024

    def test_body_truncated_at_8192(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("PAGERDUTY_ACME_ROUTING_KEY", "rk-123")
        sink = PagerDutySink(company="acme")
        captured = _mock_urlopen(mocker, {"status": "success"})
        sink.send(title="t", body="y" * 10000)
        body = json.loads(captured[0]["data"])["payload"]["custom_details"]["body"]
        assert len(body) == 8192

    def test_exception_swallowed_returns_false(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("PAGERDUTY_ACME_ROUTING_KEY", "rk-123")
        sink = PagerDutySink(company="acme")
        mocker.patch("urllib.request.urlopen", side_effect=RuntimeError("net down"))
        assert sink.send(title="t", body="b") is False
