"""briar.telemetry — opt-in error + usage analytics for the CLI.

Public surface (2 functions, 1 context manager). Everything else is
package-private:

    from briar.telemetry import install, command_span, capture_error

    install(project_config)                  # called by briar.cli.main at startup
    with command_span("extract", args):      # wraps every command dispatch
        ...

Privacy contract:
- `DO_NOT_TRACK=1` disables everything.
- `BRIAR_TELEMETRY={off,errors-only,full}` overrides the project config.
- `telemetry = "errors-only"` (or `full`) in `.briar.toml` turns it on.
- Default tier is `off`: nothing is sent until one of those says
  otherwise. No banner is printed.
- `install_id` is a random UUID; we ship its SHA256 prefix, never the
  raw id. No hostname / username / IP / paths.
- Allow-list-first tags; secret-pattern regexes; 1 KB per-value cap.
- Sentry SDK PII features hard-off; local-variable frames disabled.

If `BRIAR_SENTRY_DSN` is unset and the hardcoded default DSN is empty,
the Sentry sink silently no-ops. Code is complete and tested; ship-
ready as soon as a real DSN lands."""

from __future__ import annotations

import logging
import os
import platform
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional

from briar.telemetry._config import TelemetryConfig, TelemetryTier, resolve
from briar.telemetry._scrubber import Scrubber
from briar.telemetry._sinks import TelemetryEvent, TelemetrySink, make_sink
from briar.telemetry._sinks.noop import NoOpSink

log = logging.getLogger(__name__)


# ─── process-global state ───────────────────────────────────────────


@dataclass
class _State:
    sink: TelemetrySink = field(default_factory=NoOpSink)
    config: Optional[TelemetryConfig] = None
    scrubber: Scrubber = field(default_factory=Scrubber)
    installed: bool = False


_STATE = _State()


# ─── lifecycle ──────────────────────────────────────────────────────


def install(project_config: Optional[Mapping[str, object]] = None, version: str = "") -> TelemetryConfig:
    """Resolve config and install the global sink. Idempotent — safe
    to call multiple times. Called once by `briar.cli.main` with the
    project config it already read.

    Returns the resolved config."""
    config = resolve(project_config=project_config)
    _STATE.config = config

    if not version:
        try:
            from briar import __version__ as version
        except ImportError:
            version = "unknown"

    if config.tier is TelemetryTier.OFF:
        _STATE.sink = NoOpSink()
        log.debug("telemetry: disabled (source=%s)", config.source)
    elif not config.dsn:
        # No DSN configured: keep telemetry "enabled" semantically, but
        # the sink is no-op so no network traffic.
        _STATE.sink = NoOpSink()
        log.debug("telemetry: enabled but no DSN; using noop sink")
    else:
        _STATE.sink = make_sink(
            "sentry",
            dsn=config.dsn,
            release=f"briar-cli@{version}",
            environment=os.environ.get("BRIAR_ENV", "production"),
        )
        log.debug("telemetry: sentry sink active (tier=%s)", config.tier.value)

    _STATE.installed = True
    return config


def shutdown(*, timeout_seconds: float = 2.0) -> None:
    """Drain pending events. Best-effort; never raises."""
    try:
        _STATE.sink.flush(timeout_seconds=timeout_seconds)
        _STATE.sink.close()
    except Exception:  # noqa: BLE001
        pass


def active_config() -> Optional[TelemetryConfig]:
    """The current resolved config, or None if `install()` hasn't run."""
    return _STATE.config


# ─── capture API ────────────────────────────────────────────────────


@contextmanager
def command_span(command: str, args: Any = None, *, extra_tags: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
    """Wrap one command's lifecycle. Records duration on exit and
    captures any uncaught exception (then re-raises).

    The yielded dict is a writable bag the caller can add tags to
    mid-flight (e.g. selector action, token counts). Anything written
    there is allow-list-filtered + scrubbed before send."""
    tags: Dict[str, Any] = dict(extra_tags or {})
    tags.update(_baseline_tags(command, args))
    started = time.monotonic()
    bag: Dict[str, Any] = {}
    outcome = "ok"
    exc_type = ""
    exc_msg = ""
    try:
        yield bag
    except KeyboardInterrupt:
        outcome = "interrupt"
        raise
    except SystemExit as exc:
        outcome = "error" if exc.code else "ok"
        tags["exit_code"] = str(exc.code) if exc.code is not None else "0"
        raise
    except BaseException as exc:  # noqa: BLE001 — capture EVERYTHING then re-raise
        outcome = "error"
        exc_type = type(exc).__name__
        exc_msg = str(exc)
        raise
    finally:
        duration_ms = int((time.monotonic() - started) * 1000)
        tags["duration_ms"] = duration_ms
        tags["outcome"] = outcome
        tags.update(bag)
        try:
            _emit_command(command, outcome, duration_ms, exc_type, exc_msg, tags)
        except Exception:  # noqa: BLE001 — telemetry NEVER raises
            pass


def capture_error(exc: BaseException, *, command: str = "", extra_tags: Optional[Dict[str, Any]] = None) -> None:
    """Send an explicit error event. Most call sites should rely on
    `command_span` to do this automatically; use this only when an
    error is handled (and therefore won't propagate up through the
    span) but still worth reporting."""
    if not _STATE.installed or not _emit_allowed("error"):
        return
    tags = dict(extra_tags or {})
    tags.update(_baseline_tags(command, None))
    event = TelemetryEvent(
        kind="error",
        command=command,
        outcome="error",
        error_type=type(exc).__name__,
        error_message=_STATE.scrubber.scrub_exception_message(str(exc)),
        tags=_STATE.scrubber.scrub_tags(tags),
    )
    _STATE.sink.emit(event)


# ─── internals ─────────────────────────────────────────────────────


def _baseline_tags(command: str, args: Any) -> Dict[str, Any]:
    """Tags every event carries. `args` is the parsed argparse namespace
    (or None) — we extract FLAG NAMES ONLY, never values."""
    config = _STATE.config or resolve()
    out: Dict[str, Any] = {
        "command": command,
        "install_id": config.hashed_install_id,
        "briar_version": _briar_version(),
        "python_version": platform.python_version(),
        "os_name": platform.system().lower(),
        "os_release": platform.release()[:64],
    }
    if args is not None:
        flag_names: List[str] = []
        for key, value in vars(args).items():
            if value in (None, "", False, [], 0):
                continue
            flag_names.append(key.replace("_", "-"))
        out["flags_present"] = _STATE.scrubber.scrub_flag_names(flag_names)
    return out


def _emit_command(command: str, outcome: str, duration_ms: int, exc_type: str, exc_msg: str, tags: Dict[str, Any]) -> None:
    if not _STATE.installed:
        return
    kind = "error" if outcome == "error" else "command"
    if not _emit_allowed(kind):
        return
    cleaned_tags = _STATE.scrubber.scrub_tags(tags)
    event = TelemetryEvent(
        kind=kind,
        command=command,
        outcome=outcome,
        duration_ms=duration_ms,
        error_type=exc_type,
        error_message=_STATE.scrubber.scrub_exception_message(exc_msg) if exc_msg else "",
        tags=cleaned_tags,
    )
    _STATE.sink.emit(event)


def _emit_allowed(kind: str) -> bool:
    """Tier gate. `errors-only` lets errors through but blocks `command`
    info events; `full` lets both through; `off` is enforced at sink
    level (NoOpSink) but we double-check here for safety."""
    config = _STATE.config
    if config is None or config.tier is TelemetryTier.OFF:
        return False
    if config.tier is TelemetryTier.ERRORS_ONLY and kind != "error":
        return False
    return True


def _briar_version() -> str:
    try:
        from briar import __version__

        return __version__
    except ImportError:
        return "unknown"


# Re-exports for callers + tests.
__all__ = [
    "TelemetryConfig",
    "TelemetryEvent",
    "TelemetrySink",
    "TelemetryTier",
    "active_config",
    "capture_error",
    "command_span",
    "install",
    "shutdown",
]
