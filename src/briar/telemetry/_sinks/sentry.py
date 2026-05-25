"""Sentry sink — wraps `sentry_sdk` with the right defaults.

Why a wrapper instead of letting call sites talk to sentry_sdk directly:

1. Sentry's defaults capture too much for an OSS CLI:
   - `send_default_pii=True` ships username/IP/cookies — we want False.
   - Auto-collected `argv` exposes ticket keys, repo names — we strip it.
   - Local-variable frames in tracebacks include credentials in scope —
     `include_local_variables=False`.
2. All scrubbing decisions live in `_scrubber.py`. The SDK never sees
   raw values; it only sees the post-scrub `TelemetryEvent`.
3. Replacing Sentry later (PostHog, self-hosted OTel) is one new file
   in `_sinks/` + one registry entry, not a codebase-wide refactor."""

from __future__ import annotations

import logging

from briar.telemetry._sinks.base import TelemetryEvent, TelemetrySink

log = logging.getLogger(__name__)


class SentrySink(TelemetrySink):
    """Sends events to Sentry. Initialised once per process by `init()`;
    repeated `init(dsn=...)` calls are no-ops as long as the dsn matches."""

    name = "sentry"

    def __init__(self, *, dsn: str, release: str, environment: str = "production") -> None:
        self._dsn = dsn
        self._release = release
        self._environment = environment
        self._initialised = False

    def _ensure_init(self) -> bool:
        """Idempotent SDK initialisation. Returns True iff the SDK is
        usable. Failures (missing dsn, network down) flip back to False
        so subsequent calls no-op."""
        if self._initialised:
            return True
        if not self._dsn:
            log.debug("telemetry sentry: no DSN configured; sink will no-op")
            return False
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=self._dsn,
                release=self._release,
                environment=self._environment,
                # Hard-off: never collect username/IP/cookies. The
                # Sentry SDK defaults to False post-2.x but we set it
                # explicitly so a future SDK default flip can't surprise
                # us.
                send_default_pii=False,
                # Local-variable frames in tracebacks leak credentials
                # that happened to be in scope. Disabled.
                include_local_variables=False,
                # Tracebacks only — we set our own breadcrumbs.
                # `auto_session_tracking` adds session lifecycle pings
                # which are noise for a short-lived CLI.
                auto_session_tracking=False,
                # No SDK-collected request/url integrations — they wake
                # up on stdlib http calls and we don't want to ship URL
                # path components (which can contain ticket keys).
                default_integrations=False,
                attach_stacktrace=False,
                # Performance sampling: we ship one transaction per
                # CLI invocation manually; full transaction rate is
                # fine. Errors are always 100%.
                traces_sample_rate=1.0,
                # Never block the host. The Sentry SDK has its own
                # background worker; the only thing left is the final
                # `client.close(timeout=...)` we call from `flush()`.
                before_send=self._before_send,
                before_breadcrumb=lambda *_, **__: None,  # drop ALL breadcrumbs
            )
            self._initialised = True
            return True
        except Exception:  # noqa: BLE001 — never crash the host on init
            log.exception("telemetry sentry: init failed; sink will no-op")
            self._initialised = False
            return False

    @staticmethod
    def _before_send(event, hint):  # noqa: ARG004 — Sentry SDK contract
        """Final defence: strip request / extra / user keys the SDK may
        have populated despite our config. Returning None drops the
        event entirely."""
        if not event:
            return None
        for key in ("request", "user", "server_name", "modules", "extra"):
            event.pop(key, None)
        # Sentry SDK adds `breadcrumbs` even with the noop callback;
        # strip them anyway.
        event.pop("breadcrumbs", None)
        return event

    def emit(self, event: TelemetryEvent) -> None:
        if not self._ensure_init():
            return
        try:
            import sentry_sdk

            with sentry_sdk.isolation_scope() as scope:
                for tag_name, tag_value in event.tags.items():
                    scope.set_tag(tag_name, tag_value)
                if event.kind == "error":
                    sentry_sdk.capture_message(
                        f"{event.error_type}: {event.error_message}",
                        level="error",
                    )
                else:
                    sentry_sdk.capture_message(
                        f"briar.{event.kind}",
                        level="info",
                    )
        except Exception:  # noqa: BLE001 — telemetry NEVER raises
            log.debug("telemetry sentry: emit failed", exc_info=True)

    def flush(self, *, timeout_seconds: float = 2.0) -> None:
        if not self._initialised:
            return
        try:
            import sentry_sdk

            client = sentry_sdk.get_client()
            if client is not None:
                client.flush(timeout=timeout_seconds)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        try:
            import sentry_sdk

            client = sentry_sdk.get_client()
            if client is not None:
                client.close()
        except Exception:  # noqa: BLE001
            pass
