"""Null-object sink. Always available; used when telemetry is disabled
or when the configured sink fails to construct.

Having this as a real class instead of `Optional[TelemetrySink]` at
call sites is the Null Object pattern — every `capture_*` codepath
unconditionally calls `.emit(...)` without guarding on `is None`."""

from __future__ import annotations

from briar.telemetry._sinks.base import TelemetryEvent, TelemetrySink


class NoOpSink(TelemetrySink):
    name = "noop"

    def emit(self, event: TelemetryEvent) -> None:
        return None
