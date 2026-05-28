"""Sink registry + factory.

Mirrors the rest of the codebase's Strategy + Registry layout — each
sink is one file under `_sinks/`, `_SINK_FACTORIES` keys them by name,
and `make_sink(name, ...)` is the only construction path. Adding a
new sink is one file + one entry in the factory dict — no elif chain
to update."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

from briar._registry import build_registry
from briar.errors import CliError
from briar.telemetry._sinks.base import TelemetryEvent, TelemetrySink
from briar.telemetry._sinks.file import FileSink
from briar.telemetry._sinks.noop import NoOpSink
from briar.telemetry._sinks.sentry import SentrySink


# Sentinel instances retained for the name → class introspection path
# used by `briar telemetry status`. Real instances are constructed via
# the factory dict below — the old elif chain duplicated this mapping.
TELEMETRY_SINKS: Dict[str, TelemetrySink] = build_registry(
    (NoOpSink(), FileSink(Path("/dev/null")), SentrySink(dsn="", release="")),
    kind="telemetry sink",
)


# Per-sink factories. Each accepts arbitrary kwargs (ignored if the
# sink doesn't use them) so `make_sink(name, **everything)` works
# uniformly for every sink.
_SINK_FACTORIES: Dict[str, Callable[..., TelemetrySink]] = {
    "noop": lambda **_: NoOpSink(),
    "sentry": lambda dsn="", release="", environment="production", **_: SentrySink(
        dsn=dsn, release=release, environment=environment
    ),
    "file": lambda file_path=Path("/dev/null"), **_: FileSink(path=file_path),
}


def make_sink(name: str, **kwargs: Any) -> TelemetrySink:
    """Construct a concrete sink by registry name. Unknown name raises
    so a typo in `BRIAR_TELEMETRY_SINK=sntry` is loud, not silent."""
    factory = _SINK_FACTORIES.get(name)
    if factory is None:
        known = ", ".join(sorted(_SINK_FACTORIES))
        raise CliError(f"unknown telemetry sink: {name!r}; known: {known}")
    return factory(**kwargs)


__all__ = ["TELEMETRY_SINKS", "TelemetryEvent", "TelemetrySink", "make_sink"]
