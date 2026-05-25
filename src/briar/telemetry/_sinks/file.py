"""File sink — append-only JSON lines for local debugging.

Useful for `briar telemetry preview` (peek at exactly what would have
been sent) and for opt-in self-inspection. Never used in production;
default sinks are `sentry` (when DSN configured) or `noop`."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from briar.telemetry._sinks.base import TelemetryEvent, TelemetrySink

log = logging.getLogger(__name__)


class FileSink(TelemetrySink):
    """Writes one JSON object per line. Per-file lock unnecessary —
    each CLI process gets its own short-lived sink instance and the
    `O_APPEND` write contract is atomic on POSIX for small writes."""

    name = "file"

    def __init__(self, path: Path) -> None:
        self._path = path

    def emit(self, event: TelemetryEvent) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                json.dump(asdict(event), fh, sort_keys=True)
                fh.write("\n")
        except OSError:
            log.debug("telemetry file: write to %s failed", self._path, exc_info=True)
