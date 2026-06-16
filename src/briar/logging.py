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
from typing import IO, Optional

_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%SZ"


def configure(verbose: bool = False, *, stream: Optional[IO[str]] = None) -> None:
    """Configure root logger. INFO by default; `--verbose` → DEBUG.

    Logs to stdout by default (so the droplet's `nohup` redirects capture
    them). `stream` overrides the target — `briar mcp serve --transport stdio`
    routes logs to stderr so they never corrupt the JSON-RPC protocol stream
    that owns stdout.

    Force-reconfigures even if `logging.basicConfig` ran already, so
    test imports + repeated CLI invocations stay deterministic.
    Also attaches a `ContextFilter` so anything pushed onto
    `briar.log_context.log_context` gets prepended to every record."""
    from briar.log_context import ContextFilter

    level = logging.DEBUG if verbose else logging.INFO
    # `force=True` removes any pre-existing handlers so a test that
    # imported briar before configuring doesn't double-log.
    logging.basicConfig(
        level=level,
        format=_FORMAT,
        datefmt=_DATEFMT,
        stream=stream or sys.stdout,
        force=True,
    )
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


def _lib_debug_enabled() -> bool:
    return os.environ.get("BRIAR_LIB_DEBUG", "").strip().lower() in {"1", "true", "yes"}


def env_verbose() -> bool:
    """`BRIAR_VERBOSE=1` flips DEBUG for daemonised invocations where
    no `--verbose` flag is being passed (the dashboard / serve units)."""
    return os.environ.get("BRIAR_VERBOSE", "").strip().lower() in {"1", "true", "yes"}
