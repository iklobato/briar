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


def configure(verbose: bool = False) -> None:
    """Configure root logger. INFO by default; `--verbose` → DEBUG.

    Force-reconfigures even if `logging.basicConfig` ran already, so
    test imports + repeated CLI invocations stay deterministic."""
    level = logging.DEBUG if verbose else logging.INFO
    # `force=True` removes any pre-existing handlers so a test that
    # imported briar before configuring doesn't double-log.
    logging.basicConfig(
        level=level,
        format=_FORMAT,
        datefmt=_DATEFMT,
        stream=sys.stdout,
        force=True,
    )
    # Always log in UTC.
    logging.Formatter.converter = time.gmtime
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
