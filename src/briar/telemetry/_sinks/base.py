"""`TelemetrySink` — Strategy contract for shipping events.

Mirrors `KnowledgeStore` / `LLMProvider` / `BoardReader` etc. — Strategy
+ Registry. Each concrete sink decides where the event lands (Sentry,
a local file, /dev/null). The public API (`capture_*` in
`briar.telemetry.__init__`) is the *only* call site for these methods;
nothing else in the codebase should import a sink directly."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Dict, Optional


@dataclass(frozen=True)
class TelemetryEvent:
    """One scrubbed, ready-to-ship event. All values are strings or ints;
    the scrubber has already collapsed everything else."""

    kind: str  # "command" | "error" | "custom"
    command: str = ""
    outcome: str = "ok"  # "ok" | "error" | "interrupt"
    duration_ms: Optional[int] = None
    error_type: str = ""
    error_message: str = ""
    tags: Dict[str, str] = field(default_factory=dict)


class TelemetrySink(ABC):
    """Strategy contract — one sink per destination."""

    name: ClassVar[str] = ""

    @abstractmethod
    def emit(self, event: TelemetryEvent) -> None:
        """Ship one event. MUST be non-blocking and MUST NOT raise —
        telemetry never gets to interrupt the host program."""

    def flush(self, *, timeout_seconds: float = 2.0) -> None:
        """Best-effort drain. Called at process exit by `briar.telemetry`.
        Default impl is a no-op; sinks that buffer (Sentry) override."""

    def close(self) -> None:
        """Release any resources. Default no-op."""
