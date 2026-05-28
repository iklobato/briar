"""Tiny retry+backoff wrapper for stdlib `urllib.request.urlopen`.

`urlopen` has no built-in retry; dropping in `requests + urllib3` just
for the Retry helper is heavy for a CLI tool whose only HTTP needs are
two GraphQL endpoints (Linear, Fireflies). This module gives those
call sites bounded retry on 429 + 5xx with exponential backoff plus
full jitter — the same shape `urllib3.Retry` uses, in 30 lines.

Contract:

- Idempotent reads only. Callers using POST for GraphQL queries are
  treating each call as semantically read-only (GraphQL queries don't
  mutate); pass a mutating mutation through ``attempts=1`` to opt out.
- 4xx other than 429 is terminal — auth, validation, or contract bugs
  won't fix themselves on retry.
- 429 honours ``Retry-After`` (seconds form) up to ``max_wait``;
  otherwise falls back to the computed backoff.
- Caller wraps the result in a ``with`` block as usual; the helper
  returns the response object from the final successful ``urlopen``.
"""

from __future__ import annotations

import logging
import random
import time
import urllib.error
import urllib.request
from typing import Optional


log = logging.getLogger(__name__)


def urlopen_with_retry(
    req: urllib.request.Request,
    *,
    timeout: float,
    attempts: int = 3,
    backoff_base: float = 0.5,
    max_wait: float = 30.0,
):
    """`urlopen(req, timeout=...)` with retry on 429 + 5xx + URLError.

    Sleeps ``backoff_base * 2**(attempt-1)`` plus uniform jitter, capped
    at ``max_wait``. On final failure, re-raises the last exception so
    callers wrapped by ``@swallow_errors`` get the original signal."""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            # 4xx other than 429 is the caller's fault (auth, validation,
            # endpoint typo) — retry won't help. Surface immediately.
            if exc.code != 429 and exc.code < 500:
                raise
            if attempt >= attempts:
                break
            wait = _compute_wait(exc, attempt, backoff_base, max_wait)
            log.warning("urlopen retry attempt=%d/%d code=%d wait=%.1fs url=%s", attempt, attempts, exc.code, wait, req.full_url)
            time.sleep(wait)
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            wait = _compute_wait(None, attempt, backoff_base, max_wait)
            log.warning("urlopen retry attempt=%d/%d err=%s wait=%.1fs url=%s", attempt, attempts, type(exc).__name__, wait, req.full_url)
            time.sleep(wait)
    # The loop only exits without returning when an exception was caught
    # — so `last_exc` is always set here. The previous shape used an
    # `assert` which `python -O` strips, leaving `raise None` on the
    # next line. Same anti-pattern was removed from error_policy.py
    # in Phase 0 — fixing the regression here too.
    if last_exc is None:
        raise RuntimeError("urlopen_with_retry: loop exited without an exception or a return")
    raise last_exc


def _compute_wait(exc: Optional[urllib.error.HTTPError], attempt: int, backoff_base: float, max_wait: float) -> float:
    """Honour ``Retry-After`` (seconds form only) when present and
    within ``max_wait``; otherwise exponential backoff with full jitter."""
    if isinstance(exc, urllib.error.HTTPError):
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            try:
                seconds = float(retry_after)
                return min(max(seconds, 0.0), max_wait)
            except ValueError:
                pass  # HTTP-date form not worth parsing for two endpoints
    base = backoff_base * (2 ** (attempt - 1))
    return min(base + random.uniform(0, backoff_base), max_wait)
