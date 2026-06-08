"""TerminalPromptIO — the real input/webbrowser/poll implementation.

The device-flow acquirers depend on poll()'s exact contract (return on the
first truthy result, swallow poller exceptions, raise TimeoutError at the
deadline) and on open_url()'s headless fallback. These are pinned here with
time + webbrowser mocked so nothing sleeps or opens a browser for real.
"""

from __future__ import annotations

import pytest

from briar.auth import _prompt as prompt_mod
from briar.auth._prompt import TerminalPromptIO


@pytest.fixture
def fake_clock(mocker):  # type: ignore[no-untyped-def]
    """Monotonic clock that advances by `step` each read; no real sleeps."""
    state = {"t": 0.0, "step": 1.0, "slept": []}

    def monotonic() -> float:
        t = state["t"]
        state["t"] += state["step"]
        return t

    mocker.patch.object(prompt_mod.time, "monotonic", side_effect=monotonic)
    mocker.patch.object(prompt_mod.time, "sleep", side_effect=lambda s: state["slept"].append(s))
    return state


class TestPoll:
    def test_returns_first_truthy_result(self, fake_clock) -> None:
        fake_clock["step"] = 0.0  # clock frozen → never times out
        calls = {"n": 0}

        def fn():  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return "token" if calls["n"] == 1 else None

        assert TerminalPromptIO().poll(every=5, max_wait=900, fn=fn) == "token"
        assert calls["n"] == 1  # stopped as soon as it got a truthy value
        assert fake_clock["slept"] == []  # no sleep before the first success

    def test_keeps_polling_until_truthy(self, fake_clock) -> None:
        fake_clock["step"] = 0.0
        seq = [None, None, "tok"]
        out = TerminalPromptIO().poll(every=2, max_wait=900, fn=lambda: seq.pop(0))
        assert out == "tok"
        # Slept between the two None results (not after the success).
        assert fake_clock["slept"] == [2, 2]

    def test_poller_exception_is_swallowed_and_retried(self, fake_clock) -> None:
        fake_clock["step"] = 0.0
        calls = {"n": 0}

        def fn():  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("transient")
            return "tok"

        # A transient poll error must NOT abort the flow — it's treated as
        # "not ready yet" and retried.
        assert TerminalPromptIO().poll(every=1, max_wait=900, fn=fn) == "tok"
        assert calls["n"] == 2

    def test_times_out_at_deadline(self, fake_clock) -> None:
        fake_clock["step"] = 10.0  # each clock read jumps 10s
        with pytest.raises(TimeoutError, match="gave up after 5s"):
            TerminalPromptIO().poll(every=1, max_wait=5, fn=lambda: None)

    def test_sleep_capped_to_remaining(self, fake_clock) -> None:
        # remaining shrinks below `every` → we sleep only the remainder so we
        # never overshoot the deadline.
        fake_clock["step"] = 0.0
        seq = [None, "tok"]
        TerminalPromptIO().poll(every=1000, max_wait=900, fn=lambda: seq.pop(0))
        assert fake_clock["slept"] == [900]  # min(1000, 900)

    def test_on_tick_called_with_remaining(self, fake_clock) -> None:
        fake_clock["step"] = 0.0
        ticks = []
        seq = [None, "tok"]
        TerminalPromptIO().poll(every=1, max_wait=900, fn=lambda: seq.pop(0), on_tick=ticks.append)
        assert ticks == [900]


class TestOpenUrl:
    def test_opens_browser_when_available(self, mocker, capsys) -> None:
        import webbrowser

        opener = mocker.patch.object(webbrowser, "open", return_value=True)
        TerminalPromptIO().open_url("https://github.com/login/device")
        opener.assert_called_once_with("https://github.com/login/device", new=2)
        # When the browser opened, we do NOT also print the fallback line.
        assert "open in your browser" not in capsys.readouterr().out

    def test_prints_fallback_when_browser_unavailable(self, mocker, capsys) -> None:
        import webbrowser

        mocker.patch.object(webbrowser, "open", return_value=False)
        TerminalPromptIO().open_url("https://example.test/code")
        out = capsys.readouterr().out
        assert "https://example.test/code" in out
        assert "open in your browser" in out

    def test_prints_fallback_when_browser_raises(self, mocker, capsys) -> None:
        import webbrowser

        mocker.patch.object(webbrowser, "open", side_effect=RuntimeError("no display"))
        TerminalPromptIO().open_url("https://example.test/code")
        assert "https://example.test/code" in capsys.readouterr().out


class TestPrompt:
    def test_non_secret_uses_input(self, mocker) -> None:
        mocker.patch("builtins.input", return_value="my-answer")
        assert TerminalPromptIO().prompt("Token: ") == "my-answer"

    def test_secret_falls_back_to_getpass_without_tty(self, mocker) -> None:
        # No controlling /dev/tty (CI / piped stdin) → getpass fallback.
        import getpass

        mocker.patch("builtins.open", side_effect=OSError("no tty"))
        mocker.patch.object(getpass, "getpass", return_value="hidden-token")
        assert TerminalPromptIO().prompt("Secret: ", secret=True) == "hidden-token"

    def test_info_prints(self, capsys) -> None:
        TerminalPromptIO().info("hello operator")
        assert "hello operator" in capsys.readouterr().out
