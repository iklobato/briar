"""Sink registry + factory.

Mirrors the rest of the codebase's Strategy + Registry layout — each
sink is one file under `_sinks/`, `TELEMETRY_SINKS` keys them by
`.name`, and `make_sink(name, ...)` is the only construction path."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from briar._registry import build_registry
from briar.errors import CliError
from briar.telemetry._sinks.base import TelemetryEvent, TelemetrySink
from briar.telemetry._sinks.file import FileSink
from briar.telemetry._sinks.noop import NoOpSink
from briar.telemetry._sinks.sentry import SentrySink

# Singleton instances used purely for name → class introspection
# (e.g. by `briar telemetry status`). Real instances are constructed
# by `make_sink` with the right parameters.
TELEMETRY_SINKS: Dict[str, TelemetrySink] = build_registry(
    (NoOpSink(), FileSink(Path("/dev/null")), SentrySink(dsn="", release="")),
    kind="telemetry sink",
)


def make_sink(
    name: str,
    *,
    dsn: str = "",
    release: str = "",
    environment: str = "production",
    file_path: Path = Path("/dev/null"),
) -> TelemetrySink:
    """Construct a concrete sink. The factory exists so call sites
    don't import the concrete classes directly — adding a new sink is
    one file + one entry below."""
    if name == "noop":
        return NoOpSink()
    if name == "sentry":
        return SentrySink(dsn=dsn, release=release, environment=environment)
    if name == "file":
        return FileSink(path=file_path)
    raise CliError(f"unknown telemetry sink: {name!r}; known: {sorted(TELEMETRY_SINKS)}")


__all__ = ["TELEMETRY_SINKS", "TelemetryEvent", "TelemetrySink", "make_sink"]
