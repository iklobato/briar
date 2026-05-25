"""EmailSink — SMTP send via stdlib smtplib."""

from __future__ import annotations

import pytest

from briar.notify.email import EmailSink


@pytest.fixture
def smtp_mock(mocker):
    """Replace `smtplib.SMTP` with a MagicMock context manager.
    Returns the mock so tests can inspect login / send_message calls."""
    instance = mocker.MagicMock()
    instance.__enter__.return_value = instance
    instance.__exit__.return_value = None
    cls = mocker.patch("smtplib.SMTP", return_value=instance)
    return instance, cls


class TestAvailability:
    def test_missing_host_unavailable(self) -> None:
        assert EmailSink(company="acme").is_available() is False

    def test_with_host_from_and_to_available(self, monkeypatch) -> None:
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("EMAIL_FROM", "noreply@x")
        monkeypatch.setenv("EMAIL_ACME_TO", "ops@acme.com")
        assert EmailSink(company="acme").is_available() is True

    def test_missing_to_unavailable(self, monkeypatch) -> None:
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("EMAIL_FROM", "noreply@x")
        assert EmailSink(company="acme").is_available() is False


class TestSend:
    def test_send_with_starttls_default_true_calls_starttls(self, monkeypatch, smtp_mock) -> None:
        instance, cls = smtp_mock
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("EMAIL_FROM", "noreply@x")
        monkeypatch.setenv("EMAIL_ACME_TO", "ops@acme.com")
        monkeypatch.setenv("SMTP_USER", "u")
        monkeypatch.setenv("SMTP_PASSWORD", "p")
        assert EmailSink(company="acme").send(title="t", body="b") is True
        instance.starttls.assert_called_once()
        instance.login.assert_called_once_with("u", "p")
        instance.send_message.assert_called_once()

    def test_starttls_false_skips_starttls(self, monkeypatch, smtp_mock) -> None:
        instance, cls = smtp_mock
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("EMAIL_FROM", "noreply@x")
        monkeypatch.setenv("EMAIL_ACME_TO", "ops@acme.com")
        monkeypatch.setenv("SMTP_STARTTLS", "false")
        assert EmailSink(company="acme").send(title="t", body="b") is True
        instance.starttls.assert_not_called()

    def test_login_skipped_when_no_creds(self, monkeypatch, smtp_mock) -> None:
        instance, cls = smtp_mock
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("EMAIL_FROM", "noreply@x")
        monkeypatch.setenv("EMAIL_ACME_TO", "ops@acme.com")
        # No SMTP_USER / SMTP_PASSWORD
        assert EmailSink(company="acme").send(title="t", body="b") is True
        instance.login.assert_not_called()

    def test_multiple_recipients_comma_separated(self, monkeypatch, smtp_mock) -> None:
        instance, cls = smtp_mock
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("EMAIL_FROM", "noreply@x")
        monkeypatch.setenv("EMAIL_ACME_TO", "a@x.com, b@x.com")
        EmailSink(company="acme").send(title="t", body="b")
        msg = instance.send_message.call_args[0][0]
        assert "a@x.com" in msg["To"]
        assert "b@x.com" in msg["To"]

    def test_send_skipped_without_availability_returns_false(self, caplog_briar) -> None:
        assert EmailSink(company="acme").send(title="t", body="b") is False

    def test_smtp_exception_swallowed_returns_false(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("EMAIL_FROM", "noreply@x")
        monkeypatch.setenv("EMAIL_ACME_TO", "ops@acme.com")
        mocker.patch("smtplib.SMTP", side_effect=RuntimeError("connect refused"))
        assert EmailSink(company="acme").send(title="t", body="b") is False
