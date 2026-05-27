"""`PromptIO` — testable abstraction for interactive terminal I/O.

Every ``CredentialAcquirer`` interacts with the operator through
exactly this surface: prompt for a paste, open a browser, poll an
endpoint with progress. Concentrating these calls behind one
Protocol makes acquirers fully unit-testable — the real terminal is
swapped for a ``MockPromptIO`` that records prompts and feeds back
scripted answers.

Pattern matches what ``gh``, ``aws``, ``gcloud`` use under the hood —
their interactive helpers all funnel through a single I/O type so
test suites can drive every login flow without a real TTY."""

from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional, Protocol, Tuple


log = logging.getLogger(__name__)


class PromptIO(Protocol):
    """Terminal contract. Every acquirer's interactive surface
    funnels through these methods — no direct ``input()`` /
    ``getpass()`` / ``webbrowser.open`` calls anywhere else."""

    def prompt(self, message: str, *, secret: bool = False) -> str:
        """Read a single line from the user. ``secret=True`` suppresses
        echo (passwords, tokens)."""
        ...

    def info(self, message: str) -> None:
        """Display a single line of guidance to the operator."""
        ...

    def open_url(self, url: str) -> None:
        """Attempt to open ``url`` in the operator's browser. Implementations
        must degrade gracefully when no browser is available (over SSH,
        in a container) — just log the URL so the operator can copy it."""
        ...

    def poll(
        self,
        *,
        every: float,
        max_wait: float,
        fn: Callable[[], Optional[object]],
        on_tick: Optional[Callable[[float], None]] = None,
    ) -> object:
        """Repeatedly invoke ``fn``. Return the first non-None / non-
        empty return value. Raise ``TimeoutError`` after ``max_wait``
        seconds. Used by OAuth device flows that poll a token endpoint."""
        ...


class TerminalPromptIO:
    """Real terminal implementation — wraps ``input`` / ``getpass`` /
    ``webbrowser.open`` / ``time.sleep``."""

    def prompt(self, message: str, *, secret: bool = False) -> str:
        if secret:
            return _read_secret_no_max_canon(message)
        return input(message)

    def info(self, message: str) -> None:
        print(message)

    def open_url(self, url: str) -> None:
        """Try webbrowser.open; on failure (headless / SSH) just print
        the URL. The acquirer's narrative will already have told the
        operator to visit it manually."""
        try:
            import webbrowser

            opened = webbrowser.open(url, new=2)
        except Exception:  # noqa: BLE001
            opened = False
        if not opened:
            print(f"  open in your browser: {url}")

    def poll(
        self,
        *,
        every: float,
        max_wait: float,
        fn: Callable[[], Optional[object]],
        on_tick: Optional[Callable[[float], None]] = None,
    ) -> object:
        deadline = time.monotonic() + max_wait
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"PromptIO.poll: gave up after {max_wait}s")
            try:
                result = fn()
            except Exception:  # noqa: BLE001 — pollers handle their own retry shape
                result = None
            if result:
                return result
            if on_tick is not None:
                on_tick(remaining)
            time.sleep(min(every, remaining))


class MockPromptIO:
    """Scripted PromptIO for tests. Each ``answer`` is consumed by
    the next ``prompt`` call. ``info`` / ``open_url`` are recorded
    but otherwise ignored. ``poll`` invokes ``fn`` up to
    ``poll_attempts`` times — useful for testing device-flow polling
    without real sleeps."""

    def __init__(
        self,
        *,
        answers: Optional[List[str]] = None,
        poll_attempts: int = 1,
    ) -> None:
        self._answers: List[str] = list(answers or [])
        self._poll_attempts = poll_attempts
        self.info_log: List[str] = []
        self.opened_urls: List[str] = []
        self.prompts: List[Tuple[str, bool]] = []

    def prompt(self, message: str, *, secret: bool = False) -> str:
        self.prompts.append((message, secret))
        if not self._answers:
            raise AssertionError(f"MockPromptIO: no scripted answer for prompt {message!r}")
        return self._answers.pop(0)

    def info(self, message: str) -> None:
        self.info_log.append(message)

    def open_url(self, url: str) -> None:
        self.opened_urls.append(url)

    def poll(
        self,
        *,
        every: float,
        max_wait: float,
        fn: Callable[[], Optional[object]],
        on_tick: Optional[Callable[[float], None]] = None,
    ) -> object:
        for _ in range(self._poll_attempts):
            result = fn()
            if result:
                return result
        raise TimeoutError(f"MockPromptIO.poll: exhausted {self._poll_attempts} attempts")


def _read_secret_no_max_canon(message: str) -> str:
    """Read a single line of hidden input without hitting the terminal's
    ``MAX_CANON`` line-buffer cap.

    ``getpass.getpass`` reads ``/dev/tty`` in canonical mode, which on
    macOS / Linux caps a single line at ``MAX_CANON`` (1024 bytes on
    Darwin, often less elsewhere). Atlassian's ``tenant.session.token``
    JWT is routinely longer than that, so the trailing newline lands
    past the buffer end and Enter becomes a silent no-op — the prompt
    just sits there.

    Fix: disable ``ICANON`` (and ``ECHO``) on the controlling TTY via
    ``termios`` and read characters until newline. Falls back to
    ``getpass.getpass`` on Windows (no termios) or when there is no
    controlling TTY (CI, piped stdin) — both paths handle the buffer
    differently and are safe."""
    try:
        import termios  # POSIX-only
    except ImportError:
        import getpass

        return getpass.getpass(message)

    import os

    try:
        tty_in = open("/dev/tty", "rb", buffering=0)
        tty_out = open("/dev/tty", "wb", buffering=0)
    except OSError:
        import getpass

        return getpass.getpass(message)

    fd = tty_in.fileno()
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        tty_in.close()
        tty_out.close()
        import getpass

        return getpass.getpass(message)

    try:
        tty_out.write(message.encode("utf-8"))
        tty_out.flush()
        new = termios.tcgetattr(fd)
        # lflags index = 3; clear ICANON (line discipline) and ECHO
        new[3] &= ~(termios.ICANON | termios.ECHO)
        # cc[VMIN]=1, cc[VTIME]=0: read returns after each byte
        cc = list(new[6])
        cc[termios.VMIN] = 1
        cc[termios.VTIME] = 0
        new[6] = cc
        termios.tcsetattr(fd, termios.TCSANOW, new)

        buf = bytearray()
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            done = False
            for byte in chunk:
                if byte in (0x0A, 0x0D):  # \n or \r
                    done = True
                    break
                if byte == 0x03:  # Ctrl-C
                    raise KeyboardInterrupt
                if byte == 0x04:  # Ctrl-D — EOF
                    done = True
                    break
                if byte in (0x7F, 0x08):  # DEL / backspace
                    if buf:
                        buf.pop()
                    continue
                buf.append(byte)
            if done:
                break
        tty_out.write(b"\n")
        tty_out.flush()
        return buf.decode("utf-8", errors="replace")
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except termios.error:
            pass
        tty_in.close()
        tty_out.close()


__all__ = ["MockPromptIO", "PromptIO", "TerminalPromptIO"]
