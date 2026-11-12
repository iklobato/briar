"""What actually reaches Sentry: the real `sentry_sdk` with an in-memory transport.

The transport is the network boundary, so it is the one thing faked here.
Everything in front of it runs for real: tier resolution, the briar
scrubber, the sink, `before_send` and the SDK's `EventScrubber`."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

import briar.telemetry as telemetry
from briar.telemetry._sinks import TelemetryEvent
from briar.telemetry._sinks.sentry import SentrySink

FAKE_DSN = "https://public@example.invalid/1"
FAKE_GITHUB_TOKEN = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


class ExtractCrashed(Exception):
    """The failure a command raises inside its telemetry span."""


class RecordingTransport(Transport):
    """Keeps every event the SDK would send, sends nothing."""

    def __init__(self) -> None:
        super().__init__()
        self.events: List[Dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


@pytest.fixture
def wire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingTransport]:
    """Fresh config dir, fresh telemetry state, every `sentry_sdk.init` bound to the recorder."""
    transport = RecordingTransport()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("BRIAR_SENTRY_DSN", FAKE_DSN)
    monkeypatch.setattr(telemetry, "_STATE", telemetry._State())
    monkeypatch.setattr(sentry_sdk, "init", functools.partial(sentry_sdk.init, transport=transport))
    yield transport
    sentry_sdk.get_client().close()


class TestWhatReachesSentry:
    def _fail_inside_a_command(self, message: str) -> None:
        with pytest.raises(ExtractCrashed):
            with telemetry.command_span("extract"):
                raise ExtractCrashed(message)

    def test_default_tier_sends_no_event(self, wire: RecordingTransport) -> None:
        telemetry.install(version="2.0.0")

        self._fail_inside_a_command("boom")

        assert wire.events == []

    def test_full_tier_sends_the_error_as_extra_with_paths_scrubbed(self, wire: RecordingTransport, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_TELEMETRY", "full")
        telemetry.install(version="2.0.0")

        self._fail_inside_a_command("bad file /home/alice/acme/secrets.env")

        [event] = wire.events
        assert event["message"] == "briar.error: ExtractCrashed"
        assert event["extra"]["error_type"] == "ExtractCrashed"
        assert event["extra"]["error_message"] == "bad file <path>"
        assert event["extra"]["command"] == "extract"
        assert not set(event.get("tags", {})) & {"error_message", "command"}

    def test_token_in_the_error_message_is_redacted(self, wire: RecordingTransport, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BRIAR_TELEMETRY", "errors-only")
        telemetry.install(version="2.0.0")

        self._fail_inside_a_command("auth failed for " + FAKE_GITHUB_TOKEN)

        [event] = wire.events
        assert event["extra"]["error_message"] == "<redacted-secret>"

    def test_sentry_event_scrubber_filters_secret_named_keys(self, wire: RecordingTransport) -> None:
        # Second line behind briar's allow-list: an event that reaches the sink
        # with a secret-named key still leaves with the value filtered.
        sink = SentrySink(dsn=FAKE_DSN, release="briar-cli@2.0.0")

        sink.emit(TelemetryEvent(kind="command", command="extract", outcome="ok", tags={"password": "hunter2", "command": "extract"}))

        [event] = wire.events
        assert event["extra"]["password"] == "[Filtered]"
        assert event["extra"]["command"] == "extract"
