"""`command_span` lifecycle tests + sink capture assertions."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

import pytest

import briar.telemetry as tel
from briar.telemetry._sinks.base import TelemetryEvent, TelemetrySink


class _RecordingSink(TelemetrySink):
    name = "recording"

    def __init__(self) -> None:
        self.events: List[TelemetryEvent] = []

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)


@pytest.fixture(autouse=True)
def _isolated_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    yield


@pytest.fixture
def recording_sink(monkeypatch: pytest.MonkeyPatch) -> _RecordingSink:
    sink = _RecordingSink()
    monkeypatch.setattr(tel._STATE, "sink", sink)
    monkeypatch.setattr(tel._STATE, "installed", True)
    monkeypatch.setattr(tel._STATE, "config", tel.resolve(env={"BRIAR_TELEMETRY": "full"}))
    return sink


class TestCommandSpanHappyPath:
    def test_records_outcome_ok_and_duration(self, recording_sink: _RecordingSink) -> None:
        with tel.command_span("plan.run"):
            pass
        assert len(recording_sink.events) == 1
        ev = recording_sink.events[0]
        assert ev.kind == "command"
        assert ev.outcome == "ok"
        assert ev.tags["command"] == "plan.run"
        assert int(ev.tags["duration_ms"]) >= 0


class TestCommandSpanError:
    def test_captures_exception_and_reraises(self, recording_sink: _RecordingSink) -> None:
        with pytest.raises(RuntimeError, match="boom"):
            with tel.command_span("agent.implement"):
                raise RuntimeError("boom")
        ev = recording_sink.events[0]
        assert ev.kind == "error"
        assert ev.outcome == "error"
        assert ev.error_type == "RuntimeError"
        assert "boom" in ev.error_message

    def test_keyboard_interrupt_marks_interrupt(self, recording_sink: _RecordingSink) -> None:
        with pytest.raises(KeyboardInterrupt):
            with tel.command_span("plan.run"):
                raise KeyboardInterrupt()
        ev = recording_sink.events[0]
        assert ev.outcome == "interrupt"


class TestTierGating:
    def test_errors_only_drops_info_events(self, monkeypatch: pytest.MonkeyPatch, recording_sink: _RecordingSink) -> None:
        monkeypatch.setattr(tel._STATE, "config", tel.resolve(env={"BRIAR_TELEMETRY": "errors-only"}))
        with tel.command_span("plan.run"):
            pass
        assert recording_sink.events == []

    def test_errors_only_keeps_error_events(self, monkeypatch: pytest.MonkeyPatch, recording_sink: _RecordingSink) -> None:
        monkeypatch.setattr(tel._STATE, "config", tel.resolve(env={"BRIAR_TELEMETRY": "errors-only"}))
        with pytest.raises(ValueError):
            with tel.command_span("plan.run"):
                raise ValueError("boom")
        assert len(recording_sink.events) == 1
        assert recording_sink.events[0].kind == "error"

    def test_off_drops_everything(self, monkeypatch: pytest.MonkeyPatch, recording_sink: _RecordingSink) -> None:
        monkeypatch.setattr(tel._STATE, "config", tel.resolve(env={"BRIAR_TELEMETRY": "off"}))
        with pytest.raises(ValueError):
            with tel.command_span("plan.run"):
                raise ValueError("boom")
        assert recording_sink.events == []


class TestBaselineTags:
    def test_flag_names_only_no_values(self, recording_sink: _RecordingSink) -> None:
        ns = argparse.Namespace(llm="anthropic", company="acme", verbose=True, model="")
        with tel.command_span("plan.run", ns):
            pass
        flags = recording_sink.events[0].tags.get("flags_present", "")
        # Flag names appear:
        assert "llm" in flags
        assert "company" in flags
        # Values DON'T appear:
        assert "anthropic" not in flags
        assert "acme" not in flags

    def test_empty_flags_excluded(self, recording_sink: _RecordingSink) -> None:
        ns = argparse.Namespace(model="", limit=0, dry_run=False)
        with tel.command_span("plan.run", ns):
            pass
        flags = recording_sink.events[0].tags.get("flags_present", "")
        # All values were empty/falsey → flag list empty
        assert flags == ""

    def test_install_id_is_hashed_not_raw(self, recording_sink: _RecordingSink) -> None:
        with tel.command_span("plan.run"):
            pass
        install_id = recording_sink.events[0].tags["install_id"]
        # Hashed prefix is 16 hex chars; raw uuid is 32.
        assert len(install_id) == 16


class TestSinkNeverRaises:
    def test_sink_emit_failure_does_not_break_caller(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _BrokenSink(TelemetrySink):
            name = "broken"

            def emit(self, event):
                raise RuntimeError("network down")

        monkeypatch.setattr(tel._STATE, "sink", _BrokenSink())
        monkeypatch.setattr(tel._STATE, "installed", True)
        monkeypatch.setattr(tel._STATE, "config", tel.resolve(env={"BRIAR_TELEMETRY": "full"}))
        # The span MUST NOT raise even though the sink does.
        with tel.command_span("plan.run"):
            pass
