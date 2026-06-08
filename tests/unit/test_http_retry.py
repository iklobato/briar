"""`_http_retry.urlopen_with_retry` + `_compute_wait` — bounded retry/backoff.

The happy paths (429-then-200, 404-no-retry, Retry-After honoured, exhaust)
are pinned in tests/test_abstractions.py. This file targets the branches those
miss and the exact numeric contract of the backoff, so a mutant in the
operators (``!=``/``<``/``>=``/``2 ** (attempt - 1)``/``min``/``max``) fails:

  - the ``URLError`` retry path (connection refused / timeout, lines 63-69),
  - the ``_compute_wait`` exponential-backoff formula + jitter window + cap,
  - the ``Retry-After`` clamp (negative → 0), cap (> max_wait), and the
    HTTP-date ``ValueError`` fall-through to exponential backoff,
  - the 4xx/5xx boundary (429 retried, 499 terminal, 500 retried).
"""

from __future__ import annotations

import io
import urllib.error
from typing import List

import pytest

from briar import _http_retry
from briar._http_retry import _compute_wait, urlopen_with_retry


def _http_error(code: int, *, retry_after: str | None = None) -> urllib.error.HTTPError:
    """Build a real HTTPError with optionally a Retry-After header.

    `urllib.error.HTTPError(url, code, msg, hdrs, fp)` — `hdrs` is an
    `email.message.Message`-like object whose `.get("Retry-After")` the
    helper reads. We use a real `http.client.HTTPMessage` so `.get` behaves
    exactly as in production (case-insensitive, None when absent)."""
    from http.client import HTTPMessage

    hdrs = HTTPMessage()
    if retry_after is not None:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://x.test", code, "msg", hdrs, io.BytesIO(b""))


class _Resp:
    """Minimal stand-in for the urlopen response object the helper returns."""

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *a: object) -> None:
        return None


@pytest.fixture
def no_sleep(mocker):  # type: ignore[no-untyped-def]
    """Patch time.sleep in the module; record the durations slept."""
    slept: List[float] = []
    mocker.patch.object(_http_retry.time, "sleep", side_effect=lambda s: slept.append(s))
    return slept


@pytest.fixture
def fixed_jitter(mocker):  # type: ignore[no-untyped-def]
    """Pin random.uniform so backoff is deterministic. Returns a setter."""

    def _set(value: float) -> None:
        mocker.patch.object(_http_retry.random, "uniform", return_value=value)

    _set(0.0)
    return _set


def _req():  # type: ignore[no-untyped-def]
    import urllib.request

    return urllib.request.Request("https://x.test")


# ───────────────────────────── _compute_wait ─────────────────────────────


class TestComputeWait:
    def test_exponential_backoff_doubles_per_attempt(self, fixed_jitter) -> None:
        # base * 2**(attempt-1); jitter pinned to 0 so we see the raw curve.
        assert _compute_wait(None, attempt=1, backoff_base=0.5, max_wait=30.0) == 0.5
        assert _compute_wait(None, attempt=2, backoff_base=0.5, max_wait=30.0) == 1.0
        assert _compute_wait(None, attempt=3, backoff_base=0.5, max_wait=30.0) == 2.0
        assert _compute_wait(None, attempt=4, backoff_base=0.5, max_wait=30.0) == 4.0

    def test_jitter_added_within_zero_to_backoff_base(self, mocker) -> None:
        # The jitter term is random.uniform(0, backoff_base): full-jitter.
        # Pin it to its max (backoff_base) and assert base+jitter exactly.
        mocker.patch.object(_http_retry.random, "uniform", return_value=0.5)
        # attempt=1 base=0.5, jitter=0.5 → 1.0
        assert _compute_wait(None, attempt=1, backoff_base=0.5, max_wait=30.0) == 1.0

    def test_jitter_range_argument_is_zero_to_backoff_base(self, mocker) -> None:
        spy = mocker.patch.object(_http_retry.random, "uniform", return_value=0.0)
        _compute_wait(None, attempt=1, backoff_base=0.7, max_wait=30.0)
        spy.assert_called_once_with(0, 0.7)

    def test_backoff_capped_at_max_wait(self, fixed_jitter) -> None:
        # base alone (100) already exceeds max_wait (5) → clamped to 5.
        assert _compute_wait(None, attempt=1, backoff_base=100.0, max_wait=5.0) == 5.0

    def test_retry_after_seconds_honoured_exactly(self) -> None:
        err = _http_error(429, retry_after="7")
        assert _compute_wait(err, attempt=1, backoff_base=0.5, max_wait=30.0) == 7.0

    def test_retry_after_capped_at_max_wait(self) -> None:
        # Server asks 999s but we never wait beyond max_wait.
        err = _http_error(429, retry_after="999")
        assert _compute_wait(err, attempt=1, backoff_base=0.5, max_wait=30.0) == 30.0

    def test_retry_after_negative_clamped_to_zero(self) -> None:
        # max(seconds, 0.0): a negative/garbage-but-numeric value never
        # produces a negative sleep.
        err = _http_error(429, retry_after="-5")
        assert _compute_wait(err, attempt=1, backoff_base=0.5, max_wait=30.0) == 0.0

    def test_retry_after_http_date_falls_back_to_backoff(self, fixed_jitter) -> None:
        # HTTP-date form isn't parsed (float() raises ValueError) → the
        # helper falls through to exponential backoff, NOT a crash.
        err = _http_error(429, retry_after="Wed, 21 Oct 2099 07:28:00 GMT")
        assert _compute_wait(err, attempt=2, backoff_base=0.5, max_wait=30.0) == 1.0

    def test_retry_after_absent_uses_backoff(self, fixed_jitter) -> None:
        err = _http_error(503)  # no Retry-After header
        assert _compute_wait(err, attempt=3, backoff_base=0.5, max_wait=30.0) == 2.0

    def test_none_exc_uses_backoff(self, fixed_jitter) -> None:
        # URLError path passes exc=None → straight to backoff.
        assert _compute_wait(None, attempt=2, backoff_base=1.0, max_wait=30.0) == 2.0


# ──────────────────────────── urlopen_with_retry ─────────────────────────


class TestUrlopenWithRetry:
    def test_success_first_try_no_sleep(self, mocker, no_sleep) -> None:
        resp = _Resp()
        mocker.patch("urllib.request.urlopen", return_value=resp)
        assert urlopen_with_retry(_req(), timeout=1) is resp
        assert no_sleep == []  # never slept on a clean success

    def test_500_is_retried_then_succeeds(self, mocker, no_sleep, fixed_jitter) -> None:
        resp = _Resp()
        seq = [_http_error(500), resp]
        mocker.patch("urllib.request.urlopen", side_effect=seq)
        assert urlopen_with_retry(_req(), timeout=1, attempts=3) is resp
        assert len(no_sleep) == 1  # one backoff between the two attempts

    def test_429_is_retried_not_treated_as_terminal_4xx(self, mocker, no_sleep, fixed_jitter) -> None:
        resp = _Resp()
        mocker.patch("urllib.request.urlopen", side_effect=[_http_error(429), resp])
        assert urlopen_with_retry(_req(), timeout=1, attempts=3) is resp

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 422, 499])
    def test_4xx_other_than_429_is_terminal_first_attempt(self, mocker, no_sleep, code) -> None:
        calls = {"n": 0}

        def boom(req, timeout):  # noqa: ANN001
            calls["n"] += 1
            raise _http_error(code)

        mocker.patch("urllib.request.urlopen", side_effect=boom)
        with pytest.raises(urllib.error.HTTPError) as ei:
            urlopen_with_retry(_req(), timeout=1, attempts=5)
        assert ei.value.code == code
        assert calls["n"] == 1  # surfaced immediately, no retry, no sleep
        assert no_sleep == []

    def test_urlerror_is_retried_then_succeeds(self, mocker, no_sleep, fixed_jitter) -> None:
        # Connection refused / DNS / timeout surface as URLError, not
        # HTTPError — these are transient and must be retried.
        resp = _Resp()
        seq = [urllib.error.URLError("connection refused"), resp]
        mocker.patch("urllib.request.urlopen", side_effect=seq)
        assert urlopen_with_retry(_req(), timeout=1, attempts=3) is resp
        assert len(no_sleep) == 1

    def test_urlerror_exhausts_then_raises_the_url_error(self, mocker, no_sleep, fixed_jitter) -> None:
        err = urllib.error.URLError("timed out")
        mocker.patch("urllib.request.urlopen", side_effect=err)
        with pytest.raises(urllib.error.URLError) as ei:
            urlopen_with_retry(_req(), timeout=1, attempts=3)
        assert ei.value is err  # re-raises the LAST exception object
        assert len(no_sleep) == 2  # attempts-1 sleeps before giving up

    def test_attempts_one_means_no_retry(self, mocker, no_sleep, fixed_jitter) -> None:
        mocker.patch("urllib.request.urlopen", side_effect=_http_error(503))
        with pytest.raises(urllib.error.HTTPError):
            urlopen_with_retry(_req(), timeout=1, attempts=1)
        assert no_sleep == []  # attempts=1 → loop body never sleeps

    def test_exhausts_sleeps_exactly_attempts_minus_one_times(self, mocker, no_sleep, fixed_jitter) -> None:
        mocker.patch("urllib.request.urlopen", side_effect=_http_error(503))
        with pytest.raises(urllib.error.HTTPError):
            urlopen_with_retry(_req(), timeout=1, attempts=4)
        assert len(no_sleep) == 3  # 4 attempts → 3 inter-attempt sleeps

    def test_retry_after_drives_sleep_duration_end_to_end(self, mocker, no_sleep) -> None:
        resp = _Resp()
        mocker.patch(
            "urllib.request.urlopen",
            side_effect=[_http_error(429, retry_after="3"), resp],
        )
        assert urlopen_with_retry(_req(), timeout=1, attempts=3, max_wait=30) is resp
        assert no_sleep == [3.0]  # honoured the header, not the backoff curve

    def test_timeout_is_forwarded_to_urlopen(self, mocker, no_sleep) -> None:
        spy = mocker.patch("urllib.request.urlopen", return_value=_Resp())
        urlopen_with_retry(_req(), timeout=12.5)
        # The caller's timeout must reach urlopen — a dropped timeout would
        # let a hung endpoint block the whole CLI forever.
        assert spy.call_args.kwargs.get("timeout") == 12.5
