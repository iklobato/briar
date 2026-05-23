"""Pluggable error-response strategies.

The problem: API calls fail in many shapes (Anthropic 429,
GitHub 5xx, Bitbucket auth expired, …). A generic ``try / except
Exception`` is too coarse; a hardcoded ``if isinstance(exc, X) and
status == Y: wait Z; elif ...`` is brittle, untestable, and grows
without bound.

The shape — two ABCs, polymorphic both ways:

  ErrorPolicy.matches(exc)         → "is this MY error?"
  ErrorPolicy.decide(exc, attempt) → "given that match, what to do?"

  ErrorDecision.apply(...)         → executes the action

Both dispatches are method calls, not type checks. The
``RetryingExecutor`` body is one method-call + one binary loop check.

Composition over subclassing: ``ExceptionTypePolicy`` and
``HttpStatusPolicy`` are parameterised leaf classes you compose into
a tuple. Adding "Anthropic 529 → wait 30 minutes" is one tuple entry
in ``AnthropicLLM.default_error_policies()``, no new class.

Same Strategy + Registry shape as the rest of the codebase
(TrackerProvider, MessageWriter, JiraAuthStrategy, KnowledgeStore)."""

from __future__ import annotations

import enum
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, ClassVar, Optional, Tuple, Type, TypeVar


log = logging.getLogger(__name__)


T = TypeVar("T")


class FollowUp(enum.Enum):
    """What the executor should do after a decision runs."""

    RETRY = "retry"
    RAISE = "raise"


# ────────────────────────────── decisions ──────────────────────────────


class ErrorDecision(ABC):
    """The "what to do" half of the response. Concrete subtypes
    encapsulate the action so the executor's loop body has no
    ``isinstance`` / ``if action == ...`` branches."""

    @abstractmethod
    def apply(self, *, exc: BaseException, attempt: int) -> FollowUp:
        """Execute the action. Return FollowUp.RETRY to continue the
        executor's loop; FollowUp.RAISE to propagate the original
        exception."""


@dataclass(frozen=True)
class RetryAfter(ErrorDecision):
    """Sleep N seconds then retry. The canonical 429/503/transient
    response. ``reason`` is logged so operators can correlate spikes
    to a specific policy."""

    wait_seconds: float
    reason: str = ""

    def apply(self, *, exc: BaseException, attempt: int) -> FollowUp:
        log.warning(
            "error-policy retry-after: attempt=%d wait=%.1fs reason=%s exc=%s",
            attempt,
            self.wait_seconds,
            self.reason or "(unspecified)",
            type(exc).__name__,
        )
        if self.wait_seconds > 0:
            time.sleep(self.wait_seconds)
        return FollowUp.RETRY


@dataclass(frozen=True)
class Abort(ErrorDecision):
    """Don't retry — propagate the exception. Default for 401/403,
    schema errors, anything that won't change on the next try."""

    reason: str = ""

    def apply(self, *, exc: BaseException, attempt: int) -> FollowUp:
        log.error(
            "error-policy abort: attempt=%d reason=%s exc=%s",
            attempt,
            self.reason or "(unspecified)",
            type(exc).__name__,
        )
        return FollowUp.RAISE


@dataclass(frozen=True)
class Escalate(ErrorDecision):
    """Notify the operator via a configured dispatcher, then either
    retry or raise. The dispatcher is a callable injected by the
    consumer — keeps this module decoupled from `briar/notify/`."""

    dispatcher: Callable[[str], None]
    message: str
    then: FollowUp = FollowUp.RAISE
    wait_seconds: float = 0.0

    def apply(self, *, exc: BaseException, attempt: int) -> FollowUp:
        log.error(
            "error-policy escalate: attempt=%d then=%s message=%s exc=%s",
            attempt,
            self.then.value,
            self.message,
            type(exc).__name__,
        )
        try:
            self.dispatcher(self.message)
        except Exception:  # noqa: BLE001
            log.exception("error-policy escalate: dispatcher itself failed; continuing with then=%s", self.then.value)
        if self.wait_seconds > 0:
            time.sleep(self.wait_seconds)
        return self.then


# ─────────────────────────────── policies ──────────────────────────────


class ErrorPolicy(ABC):
    """One error-class → one decision. Policies are stateless and
    reusable across the process. Composed into an ``ErrorPolicyRegistry``
    in priority order; first ``matches`` wins."""

    kind: ClassVar[str] = ""

    @abstractmethod
    def matches(self, exc: BaseException) -> bool: ...

    @abstractmethod
    def decide(self, exc: BaseException, attempt: int) -> ErrorDecision: ...


@dataclass(frozen=True)
class ExceptionTypePolicy(ErrorPolicy):
    """Match by exception class (inclusive of subclasses, per
    ``isinstance``). The most common policy shape — one entry per
    SDK exception you care about."""

    exception_type: Type[BaseException]
    decision: ErrorDecision
    kind: ClassVar[str] = "exception-type"

    def matches(self, exc: BaseException) -> bool:
        return isinstance(exc, self.exception_type)

    def decide(self, exc: BaseException, attempt: int) -> ErrorDecision:
        return self.decision


@dataclass(frozen=True)
class HttpStatusPolicy(ErrorPolicy):
    """Match by exception class AND HTTP status. Useful when one SDK
    raises one exception type for many status codes (e.g.
    ``anthropic.APIStatusError``: 401, 403, 500, 503, 529, …)."""

    exception_type: Type[BaseException]
    status: int
    decision: ErrorDecision
    status_attr: str = "status_code"
    kind: ClassVar[str] = "http-status"

    def matches(self, exc: BaseException) -> bool:
        if not isinstance(exc, self.exception_type):
            return False
        actual = getattr(exc, self.status_attr, None)
        return actual == self.status

    def decide(self, exc: BaseException, attempt: int) -> ErrorDecision:
        return self.decision


@dataclass(frozen=True)
class _PropagatePolicy(ErrorPolicy):
    """Null-object policy — matches everything, decides to Abort.
    Lives at the tail of every registry so unmatched exceptions get
    a structured Abort decision instead of a None at the call site."""

    kind: ClassVar[str] = "propagate"

    def matches(self, exc: BaseException) -> bool:
        return True

    def decide(self, exc: BaseException, attempt: int) -> ErrorDecision:
        return Abort(reason=f"no policy matched {type(exc).__name__}")


_PROPAGATE = _PropagatePolicy()


# ────────────────────────────── registry ───────────────────────────────


@dataclass(frozen=True)
class ErrorPolicyRegistry:
    """Ordered, immutable tuple of policies. First match wins.

    The registry ALWAYS resolves to a policy — the tail ``_PROPAGATE``
    null-object guarantees no caller has to check ``policy is None``."""

    policies: Tuple[ErrorPolicy, ...] = ()

    def resolve(self, exc: BaseException) -> ErrorPolicy:
        return next(
            (p for p in self.policies if p.matches(exc)),
            _PROPAGATE,
        )

    def with_(self, *extra: ErrorPolicy) -> "ErrorPolicyRegistry":
        """Return a new registry with ``extra`` policies prepended
        (higher priority than the existing ones). Useful for
        per-company overlays on top of a provider's defaults."""
        return ErrorPolicyRegistry(policies=tuple(extra) + self.policies)


# ────────────────────────────── executor ───────────────────────────────


class RetryingExecutor:
    """Wraps a zero-arg callable with a registry. On each exception,
    asks the registry for the matching policy, asks the policy for a
    decision, asks the decision to apply itself. Zero type-dispatch
    in the body — both directions are polymorphic."""

    def __init__(self, registry: ErrorPolicyRegistry, *, max_attempts: int = 10) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
        self._registry = registry
        self._max_attempts = max_attempts

    def run(self, fn: Callable[[], T]) -> T:
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return fn()
            except BaseException as exc:  # noqa: BLE001 — re-raised after policy decides
                last_exc = exc
                decision = self._registry.resolve(exc).decide(exc, attempt)
                if decision.apply(exc=exc, attempt=attempt) is FollowUp.RAISE:
                    raise
        log.error("error-policy exhausted: %d attempts, last exc=%s", self._max_attempts, type(last_exc).__name__ if last_exc else "?")
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"RetryingExecutor: exhausted {self._max_attempts} attempts with no exception captured")


__all__ = [
    "Abort",
    "ErrorDecision",
    "ErrorPolicy",
    "ErrorPolicyRegistry",
    "Escalate",
    "ExceptionTypePolicy",
    "FollowUp",
    "HttpStatusPolicy",
    "RetryAfter",
    "RetryingExecutor",
]
