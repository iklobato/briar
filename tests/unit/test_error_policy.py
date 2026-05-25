"""Error-policy strategies — composability + retry semantics.

Catches: max_attempts off-by-one, wait_seconds<=0 sleep elision,
HttpStatusPolicy missing-attr false-match, registry resolve never-None
invariant, executor exhaustion re-raises original exception."""

from __future__ import annotations

import logging

import pytest

from briar.error_policy import (
    Abort,
    ErrorPolicyRegistry,
    Escalate,
    ExceptionTypePolicy,
    FollowUp,
    HttpStatusPolicy,
    RetryAfter,
    RetryingExecutor,
    _PROPAGATE,
)


class _FakeHttpError(Exception):
    def __init__(self, status: int, msg: str = "") -> None:
        self.status_code = status
        super().__init__(msg)


# ─── decisions ────────────────────────────────────────────────────────


class TestRetryAfter:
    def test_returns_retry(self) -> None:
        d = RetryAfter(wait_seconds=0)
        assert d.apply(exc=RuntimeError(), attempt=1) is FollowUp.RETRY

    def test_sleeps_when_wait_positive(self, mocker) -> None:
        sleep = mocker.patch("briar.error_policy.time.sleep")
        RetryAfter(wait_seconds=1.5).apply(exc=RuntimeError(), attempt=1)
        sleep.assert_called_once_with(1.5)

    @pytest.mark.parametrize("wait", [0, -1, -10.0])
    def test_skips_sleep_when_wait_zero_or_negative(self, mocker, wait) -> None:
        sleep = mocker.patch("briar.error_policy.time.sleep")
        RetryAfter(wait_seconds=wait).apply(exc=RuntimeError(), attempt=1)
        sleep.assert_not_called()

    def test_logs_attempt_one_indexed(self, caplog_briar) -> None:
        caplog_briar.set_level(logging.WARNING, logger="briar.error_policy")
        RetryAfter(wait_seconds=0, reason="rate-limit").apply(exc=RuntimeError("x"), attempt=3)
        msgs = " ".join(r.message for r in caplog_briar.records)
        assert "attempt=3" in msgs
        assert "rate-limit" in msgs


class TestAbort:
    def test_returns_raise(self) -> None:
        assert Abort().apply(exc=RuntimeError(), attempt=1) is FollowUp.RAISE


class TestEscalate:
    def test_calls_dispatcher_with_message(self) -> None:
        calls: list[str] = []
        Escalate(dispatcher=calls.append, message="alert!").apply(exc=RuntimeError(), attempt=1)
        assert calls == ["alert!"]

    def test_dispatcher_failure_swallowed_returns_then(self, caplog_briar) -> None:
        def boom(_: str) -> None:
            raise RuntimeError("notify broke")

        result = Escalate(dispatcher=boom, message="x", then=FollowUp.RETRY).apply(exc=RuntimeError(), attempt=1)
        assert result is FollowUp.RETRY
        # The dispatcher failure should have been logged.
        assert any("dispatcher itself failed" in r.message for r in caplog_briar.records)

    def test_sleeps_after_dispatcher_when_wait_positive(self, mocker) -> None:
        sleep = mocker.patch("briar.error_policy.time.sleep")
        Escalate(dispatcher=lambda _: None, message="x", wait_seconds=2).apply(exc=RuntimeError(), attempt=1)
        sleep.assert_called_once_with(2)


# ─── policies ─────────────────────────────────────────────────────────


class TestExceptionTypePolicy:
    def test_matches_exact_type(self) -> None:
        p = ExceptionTypePolicy(ValueError, Abort())
        assert p.matches(ValueError("x")) is True

    def test_matches_subclass(self) -> None:
        # isinstance semantics — UnicodeError ⊂ ValueError
        p = ExceptionTypePolicy(ValueError, Abort())
        assert p.matches(UnicodeError("x")) is True

    def test_does_not_match_sibling_type(self) -> None:
        p = ExceptionTypePolicy(ValueError, Abort())
        assert p.matches(KeyError("x")) is False


class TestHttpStatusPolicy:
    def test_matches_type_and_status(self) -> None:
        p = HttpStatusPolicy(_FakeHttpError, 429, RetryAfter(0))
        assert p.matches(_FakeHttpError(429)) is True

    def test_type_match_status_mismatch_false(self) -> None:
        p = HttpStatusPolicy(_FakeHttpError, 429, RetryAfter(0))
        assert p.matches(_FakeHttpError(500)) is False

    def test_status_match_type_mismatch_false(self) -> None:
        p = HttpStatusPolicy(_FakeHttpError, 429, RetryAfter(0))
        other = RuntimeError()
        other.status_code = 429  # type: ignore[attr-defined]
        assert p.matches(other) is False

    def test_missing_status_attr_does_not_match(self) -> None:
        # `getattr(exc, 'status_code', None)` returns None which != 429.
        p = HttpStatusPolicy(_FakeHttpError, 429, RetryAfter(0))
        exc = _FakeHttpError(0)
        del exc.status_code
        assert p.matches(exc) is False

    def test_custom_status_attr(self) -> None:
        class _E(Exception):
            def __init__(self, n: int) -> None:
                self.http_status = n

        p = HttpStatusPolicy(_E, 503, Abort(), status_attr="http_status")
        assert p.matches(_E(503)) is True
        assert p.matches(_E(500)) is False


# ─── registry ─────────────────────────────────────────────────────────


class TestRegistry:
    def test_empty_registry_resolves_to_propagate(self) -> None:
        r = ErrorPolicyRegistry()
        assert r.resolve(RuntimeError()) is _PROPAGATE

    def test_first_match_wins(self) -> None:
        first = ExceptionTypePolicy(ValueError, Abort(reason="first"))
        second = ExceptionTypePolicy(ValueError, RetryAfter(0, reason="second"))
        r = ErrorPolicyRegistry(policies=(first, second))
        assert r.resolve(ValueError()) is first

    def test_no_match_falls_through_to_propagate(self) -> None:
        r = ErrorPolicyRegistry(policies=(ExceptionTypePolicy(KeyError, Abort()),))
        assert r.resolve(ValueError()) is _PROPAGATE

    def test_with_prepends_higher_priority(self) -> None:
        existing = ExceptionTypePolicy(ValueError, Abort(reason="default"))
        override = ExceptionTypePolicy(ValueError, RetryAfter(0, reason="overlay"))
        base = ErrorPolicyRegistry(policies=(existing,))
        composed = base.with_(override)
        assert composed.resolve(ValueError()) is override
        # original is untouched
        assert base.resolve(ValueError()) is existing

    def test_propagate_decides_abort(self) -> None:
        decision = _PROPAGATE.decide(RuntimeError(), 1)
        assert isinstance(decision, Abort)


# ─── executor ─────────────────────────────────────────────────────────


class TestRetryingExecutor:
    @pytest.mark.parametrize("n", [0, -1])
    def test_construction_rejects_max_attempts_lt_1(self, n: int) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            RetryingExecutor(ErrorPolicyRegistry(), max_attempts=n)

    def test_success_on_first_call_returns_value(self) -> None:
        ex = RetryingExecutor(ErrorPolicyRegistry(), max_attempts=3)
        assert ex.run(lambda: "ok") == "ok"

    @pytest.mark.parametrize("val", [0, "", [], False, None])
    def test_falsy_return_value_is_not_a_retry_signal(self, val: object) -> None:
        ex = RetryingExecutor(ErrorPolicyRegistry(), max_attempts=3)
        assert ex.run(lambda: val) == val

    def test_max_attempts_1_no_retry(self) -> None:
        calls = []

        def fn() -> None:
            calls.append(1)
            raise ValueError("nope")

        registry = ErrorPolicyRegistry(policies=(ExceptionTypePolicy(ValueError, RetryAfter(0)),))
        ex = RetryingExecutor(registry, max_attempts=1)
        with pytest.raises(ValueError, match="nope"):
            ex.run(fn)
        assert len(calls) == 1

    def test_retry_then_succeed(self, mocker) -> None:
        mocker.patch("briar.error_policy.time.sleep")
        attempts = []

        def fn() -> str:
            attempts.append(len(attempts) + 1)
            if len(attempts) < 3:
                raise ValueError("transient")
            return "ok"

        registry = ErrorPolicyRegistry(policies=(ExceptionTypePolicy(ValueError, RetryAfter(0)),))
        ex = RetryingExecutor(registry, max_attempts=5)
        assert ex.run(fn) == "ok"
        assert attempts == [1, 2, 3]

    def test_exhaustion_reraises_last_exception(self, mocker) -> None:
        mocker.patch("briar.error_policy.time.sleep")
        seen = []

        def fn() -> None:
            seen.append(len(seen) + 1)
            raise ValueError(f"attempt-{seen[-1]}")

        registry = ErrorPolicyRegistry(policies=(ExceptionTypePolicy(ValueError, RetryAfter(0)),))
        ex = RetryingExecutor(registry, max_attempts=3)
        with pytest.raises(ValueError, match="attempt-3"):
            ex.run(fn)
        assert len(seen) == 3

    def test_abort_decision_raises_immediately(self) -> None:
        attempts = []

        def fn() -> None:
            attempts.append(1)
            raise ValueError("perm")

        registry = ErrorPolicyRegistry(policies=(ExceptionTypePolicy(ValueError, Abort()),))
        ex = RetryingExecutor(registry, max_attempts=10)
        with pytest.raises(ValueError):
            ex.run(fn)
        assert len(attempts) == 1  # no retry

    def test_unmatched_exception_propagates_via_default_abort(self) -> None:
        attempts = []

        def fn() -> None:
            attempts.append(1)
            raise KeyError("unknown")

        registry = ErrorPolicyRegistry(policies=(ExceptionTypePolicy(ValueError, RetryAfter(0)),))
        ex = RetryingExecutor(registry, max_attempts=10)
        with pytest.raises(KeyError):
            ex.run(fn)
        assert len(attempts) == 1
