"""Context-aware logging — stamps every log record in the active
context with the (company, task, extractor, ...) breadcrumb.

Usage:

    from briar.log_context import log_context

    with log_context(company="acme", task="prfix"):
        log.info("starting cycle")            # → "[acme/prfix] starting cycle"
        with log_context(extractor="active-work"):
            log.info("fetching repos")        # → "[acme/prfix/active-work] fetching repos"

The implementation uses a `contextvars.ContextVar` so it is async-safe
and thread-safe, plus a `logging.Filter` that turns the active mapping
into a `[k=v k=v]` prefix on the `message`. We rewrite the message
instead of relying on an `extra=` slot so the prefix shows up in every
formatter without requiring per-format changes."""

from __future__ import annotations

import contextlib
import contextvars
import logging
from typing import Any, Dict, Iterator


_CTX: contextvars.ContextVar[Dict[str, str]] = contextvars.ContextVar("briar_log_ctx", default={})


@contextlib.contextmanager
def log_context(**bindings: Any) -> Iterator[None]:
    """Push key=value pairs onto the active log context for the
    duration of the `with` block. Nested calls extend (not replace)
    the parent context."""
    current = dict(_CTX.get())
    for key, value in bindings.items():
        current[key] = str(value)
    token = _CTX.set(current)
    try:
        yield
    finally:
        _CTX.reset(token)


def current_context() -> Dict[str, str]:
    """Read-only snapshot of the active bindings. Useful for callers
    that want to ferry breadcrumbs into structured payloads, not just
    log lines."""
    return dict(_CTX.get())


class ContextFilter(logging.Filter):
    """Filter that prepends `[k1=v1 k2=v2]` to every log record's
    message when the context has bindings. Attached to the root logger
    by `briar.logging.configure`.

    The prefix is applied via ``record.msg`` rewriting BUT only once
    per record: subsequent passes by the same filter instance (e.g.
    when a record propagates through multiple handlers) skip the
    rewrite via the ``_briar_ctx_applied`` sentinel attribute. This
    prevents the double-prefix that would otherwise occur on records
    formatted by two handlers."""

    _ORDER = ("company", "task", "extractor", "shape", "repo")
    _APPLIED_FLAG = "_briar_ctx_applied"

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _CTX.get()
        if not ctx:
            return True
        # Idempotency guard — if any ContextFilter (this instance or
        # another attached to a sibling handler) already prefixed this
        # record, don't double-stamp it.
        if getattr(record, self._APPLIED_FLAG, False):
            return True
        ordered: list = []
        for key in self._ORDER:
            value = ctx.get(key)
            if value:
                ordered.append(f"{key}={value}")
        for key, value in ctx.items():
            if key not in self._ORDER:
                ordered.append(f"{key}={value}")
        prefix = "[" + " ".join(ordered) + "] "
        record.msg = prefix + str(record.msg)
        setattr(record, self._APPLIED_FLAG, True)
        return True
