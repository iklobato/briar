"""Shared decorators used across the codebase.

Right now: one decorator (`swallow_errors`) used by every external-API
adapter (RepositoryProvider, TrackerProvider, LLMProvider,
CloudProvider). The "wrap one verb in try/except and log + return
default on failure" shape appeared 5× in BitbucketProvider alone; with
4 more adapter families on the way it would have appeared ~25 times.

Per the project's Python style rules (CLAUDE.md): when the same
``try: ... except: log+swallow`` shape shows up in three or more places,
extract a decorator. This is that file."""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, TypeVar


log = logging.getLogger(__name__)


T = TypeVar("T")


def swallow_errors(*, default: Any = None, message: str = "") -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator: log any *runtime* exception and return ``default``
    instead of raising. Use on external-API adapter verbs where a
    single failed call shouldn't kill the whole extract run.

    ``default`` is what gets returned on failure — usually ``[]`` for
    list-returning verbs, ``""`` for string-returning verbs.
    ``message`` is the log prefix (defaults to the qualified function
    name). The traceback is captured automatically via
    ``log.exception``.

    Caller errors (``ValueError``, ``TypeError``, ``AssertionError``)
    are NOT swallowed — those signal a programming bug or invalid
    input that the caller needs to see. Swallowing them used to mask
    edge-input validation failures (jira project regex, github
    owner/repo format) as "no results," which then looked like a
    legitimate empty response downstream."""

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except (ValueError, TypeError, AssertionError):
                # Caller error — surface as bugs, don't paper over.
                raise
            except Exception:  # noqa: BLE001 — runtime/external failures
                log.exception("%s failed", message or fn.__qualname__)
                return default

        return wrapper

    return decorator
