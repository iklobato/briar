"""Logging configuration — one place to set the format, level, and
output stream so every module can `logging.getLogger(__name__)` and
get consistent output across the CLI, scheduler, and dashboard.

Output shape (line-prefix):
    2026-05-20T15:32:19Z [INFO   ] briar.iac.runbook.scheduler: …

Always UTC. Always to stdout (so the existing `nohup` redirects on
the droplet capture into `scheduler.log` / `dashboard.log`). Errors
caught in broad-except sites should call `logger.exception(...)`,
which appends the traceback automatically."""

from __future__ import annotations

import logging
import os
import sys
import time

_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%SZ"


_LEVEL_NAMES = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _resolve_level(verbose: bool) -> int:
    """Effective log level. `--verbose`/`BRIAR_VERBOSE` → DEBUG; else an
    explicit `BRIAR_LOG_LEVEL` (DEBUG/INFO/WARNING/ERROR) if set; else
    WARNING. Quiet-by-default keeps one-shot commands from spewing
    operational INFO at users — daemons opt back in via `daemon_logging()`
    or `BRIAR_LOG_LEVEL=INFO`."""
    if verbose or env_verbose():
        return logging.DEBUG
    explicit = os.environ.get("BRIAR_LOG_LEVEL", "").strip().upper()
    return _LEVEL_NAMES.get(explicit, logging.WARNING)


def configure(verbose: bool = False) -> None:
    """Configure root logger. WARNING by default (quiet); `--verbose` →
    DEBUG; `BRIAR_LOG_LEVEL` overrides the default.

    Logs go to STDERR so stdout stays a clean machine-readable channel
    for piping (`--format json | jq …`).

    Force-reconfigures even if `logging.basicConfig` ran already, so
    test imports + repeated CLI invocations stay deterministic.
    Also attaches a `ContextFilter` so anything pushed onto
    `briar.log_context.log_context` gets prepended to every record."""
    from briar.log_context import ContextFilter

    level = _resolve_level(verbose)
    # `force=True` removes any pre-existing handlers so a test that
    # imported briar before configuring doesn't double-log.
    logging.basicConfig(
        level=level,
        format=_FORMAT,
        datefmt=_DATEFMT,
        stream=sys.stderr,
        force=True,
    )
    # Clear any per-run override on briar's own logger (e.g. a prior
    # `daemon_logging()` bump) so reconfiguration is idempotent and the
    # root level governs again.
    logging.getLogger("briar").setLevel(logging.NOTSET)
    # Always log in UTC.
    logging.Formatter.converter = time.gmtime
    # Attach the context filter to every existing handler. New handlers
    # added later (e.g. by tests) will not get it automatically; that is
    # intentional — production never adds handlers after configure().
    context_filter = ContextFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(context_filter)
    # Quiet noisy third-party libs regardless of --verbose. The flag is
    # meant to turn up briar's own DEBUG output, not bury us in every
    # TLS handshake. Use BRIAR_LIB_DEBUG=1 to flip these on.
    if not _lib_debug_enabled():
        for noisy in ("httpx", "httpcore", "urllib3", "schedule", "boto3", "botocore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.captureWarnings(True)


def daemon_logging() -> None:
    """Raise briar's own loggers to at least INFO for long-running
    operational commands (`runbook serve`, `runbook extract`) where
    progress visibility matters. No-op when already at DEBUG/INFO (so
    `--verbose` still wins). Other commands stay quiet-by-default."""
    briar_logger = logging.getLogger("briar")
    if briar_logger.getEffectiveLevel() > logging.INFO:
        briar_logger.setLevel(logging.INFO)


def _lib_debug_enabled() -> bool:
    return os.environ.get("BRIAR_LIB_DEBUG", "").strip().lower() in {"1", "true", "yes"}


def env_verbose() -> bool:
    """`BRIAR_VERBOSE=1` flips DEBUG for daemonised invocations where
    no `--verbose` flag is being passed (the dashboard / serve units)."""
    return os.environ.get("BRIAR_VERBOSE", "").strip().lower() in {"1", "true", "yes"}
