"""DashboardServer HTTP layer + Jinja filters + resilient Undefined.

We do NOT bind a real socket: the request handler is driven through a
fake socket (a BytesIO request fed to ``BaseHTTPRequestHandler``), so the
routing / 404 / 500 / HEAD / security-header behaviour is exercised
in-process. ``render_index`` is stubbed at the seam so handler tests
don't depend on the full template's collector set (render isolation
itself is covered by test_server_render.py).
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest

from briar.dashboard.server import DashboardServer, _build_handler, _human_age, _human_bytes, _ResilientUndefined, _short_id

# ── HTTP handler: drive without a real socket ────────────────────────


class _FakeSocket:
    """Minimal socket stand-in: serves the canned request bytes on
    makefile('rb') and captures everything written on makefile('wb')."""

    def __init__(self, request_bytes: bytes) -> None:
        self._rfile = io.BytesIO(request_bytes)
        self.wfile = io.BytesIO()

    def makefile(self, mode: str, *args, **kwargs):  # noqa: ANN001
        return self.wfile if "w" in mode else self._rfile

    def sendall(self, data):  # noqa: ANN001
        self.wfile.write(data)


def _drive(dashboard: DashboardServer, raw_request: str) -> bytes:
    """Run one request through the handler and return the raw response."""
    handler_cls = _build_handler(dashboard)
    sock = _FakeSocket(raw_request.encode("latin-1"))
    handler_cls(sock, ("127.0.0.1", 54321), None)
    return sock.wfile.getvalue()


@pytest.fixture
def dashboard(mocker):
    d = DashboardServer()
    # Stub render so handler tests don't need the full template/collector set.
    mocker.patch.object(d, "render_index", return_value="<html>BODY</html>")
    return d


class TestRouting:
    def test_root_renders_index_with_html_content_type(self, dashboard) -> None:
        resp = _drive(dashboard, "GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"200 OK" in resp.split(b"\r\n", 1)[0]
        assert b"Content-Type: text/html; charset=utf-8" in resp
        assert resp.endswith(b"<html>BODY</html>")

    def test_root_increments_request_count(self, dashboard) -> None:
        assert dashboard.request_count() == 0
        _drive(dashboard, "GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert dashboard.request_count() == 1

    def test_healthz_returns_ok_plaintext(self, dashboard) -> None:
        resp = _drive(dashboard, "GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"200 OK" in resp.split(b"\r\n", 1)[0]
        assert b"Content-Type: text/plain; charset=utf-8" in resp
        assert resp.endswith(b"ok\n")

    def test_query_string_is_stripped_for_routing(self, dashboard) -> None:
        # `/?foo=bar` must still route to the index handler.
        resp = _drive(dashboard, "GET /?foo=bar HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"200 OK" in resp.split(b"\r\n", 1)[0]
        assert resp.endswith(b"<html>BODY</html>")

    def test_unknown_path_returns_404(self, dashboard) -> None:
        resp = _drive(dashboard, "GET /nope HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"404 Not Found" in resp.split(b"\r\n", 1)[0]
        assert resp.endswith(b"not found")

    def test_security_headers_present_on_index(self, dashboard) -> None:
        resp = _drive(dashboard, "GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"Cache-Control: no-store" in resp
        assert b"X-Content-Type-Options: nosniff" in resp
        assert b"Referrer-Policy: no-referrer" in resp
        assert b"Content-Length: " + str(len("<html>BODY</html>")).encode() in resp


class TestHandlerErrors:
    def test_render_crash_returns_500(self, dashboard, mocker) -> None:
        # A handler-level exception (not a per-collector one) → 500, not a
        # dropped connection.
        mocker.patch.object(dashboard, "render_index", side_effect=RuntimeError("render exploded"))
        resp = _drive(dashboard, "GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"500 Internal Server Error" in resp.split(b"\r\n", 1)[0]
        assert resp.endswith(b"internal error\n")


class TestHead:
    def test_head_sends_headers_but_no_body(self, dashboard) -> None:
        resp = _drive(dashboard, "HEAD / HTTP/1.1\r\nHost: x\r\n\r\n")
        head_line, _, rest = resp.partition(b"\r\n")
        assert b"200 OK" in head_line
        # Content-Length must still advertise the body size...
        assert b"Content-Length: " + str(len("<html>BODY</html>")).encode() in resp
        # ...but the body itself must be absent (HEAD).
        assert not resp.endswith(b"<html>BODY</html>")

    def test_head_on_missing_path_is_404_without_body(self, dashboard) -> None:
        resp = _drive(dashboard, "HEAD /nope HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"404 Not Found" in resp.split(b"\r\n", 1)[0]
        assert not resp.endswith(b"not found")


class TestUnsupportedMethod:
    def test_post_is_not_handled_read_only_server(self, dashboard) -> None:
        # Only GET/HEAD are registered; POST falls through to http.server's
        # 501 default — the server is read-only by construction.
        resp = _drive(dashboard, "POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n")
        assert b"501" in resp.split(b"\r\n", 1)[0]


# ── Jinja filters ────────────────────────────────────────────────────


class TestHumanBytes:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, "0 B"),
            (512, "512 B"),
            (1024, "1.0 KB"),
            (3725, "3.6 KB"),
            (1024 * 1024, "1.0 MB"),
            (1024**3, "1.0 GB"),
        ],
    )
    def test_scales(self, value, expected) -> None:
        assert _human_bytes(value) == expected

    def test_negative_clamped_to_zero(self) -> None:
        assert _human_bytes(-5) == "0 B"

    def test_non_numeric_returns_empty(self) -> None:
        assert _human_bytes("not a number") == ""
        assert _human_bytes(None) == ""

    def test_petabyte_overflow_branch(self) -> None:
        # Larger than TB → the trailing PB return.
        assert _human_bytes(1024**5).endswith("PB")


class TestHumanAge:
    def test_blank_returns_empty(self) -> None:
        assert _human_age("") == ""
        assert _human_age(None) == ""

    def test_unparseable_returns_empty(self) -> None:
        assert _human_age("not-a-timestamp") == ""

    def test_seconds_minutes_hours_days(self) -> None:
        now = datetime.now(timezone.utc)
        assert _human_age((now - timedelta(seconds=5)).isoformat()) == "5s ago"
        assert _human_age((now - timedelta(minutes=3)).isoformat()) == "3m ago"
        assert _human_age((now - timedelta(hours=2)).isoformat()) == "2h ago"
        assert _human_age((now - timedelta(days=4)).isoformat()) == "4d ago"

    def test_future_timestamp(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert _human_age(future) == "in the future"

    def test_naive_timestamp_assumed_utc(self) -> None:
        # No tzinfo → treated as UTC; a few seconds back reads as "Ns ago".
        naive = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=2)).isoformat()
        assert _human_age(naive).endswith("s ago")


class TestShortId:
    def test_truncates_to_eight(self) -> None:
        assert _short_id("0123456789abcdef") == "01234567"

    def test_short_input_unchanged(self) -> None:
        assert _short_id("abc") == "abc"

    def test_none_returns_empty(self) -> None:
        assert _short_id(None) == ""


# ── _ResilientUndefined: the per-section render safety net ────────────


class TestResilientUndefined:
    def _u(self) -> _ResilientUndefined:
        return _ResilientUndefined(name="missing")

    def test_iteration_yields_nothing(self) -> None:
        assert list(self._u()) == []

    def test_length_is_zero(self) -> None:
        assert len(self._u()) == 0

    def test_str_is_blank(self) -> None:
        assert str(self._u()) == ""

    def test_numeric_coercions(self) -> None:
        assert int(self._u()) == 0
        assert float(self._u()) == 0.0

    def test_comparisons_are_false(self) -> None:
        u = self._u()
        assert (u < 1) is False
        assert (u <= 1) is False
        assert (u > 1) is False
        assert (u >= 1) is False

    def test_string_concatenation(self) -> None:
        u = self._u()
        assert u + "x" == ""
        assert "prefix-" + u == "prefix-"

    def test_renders_failed_section_to_blanks_not_500(self) -> None:
        # End-to-end: a template that touches missing fields with the
        # operations the dashboard uses must render, not raise.
        from jinja2 import Environment

        env = Environment(undefined=_ResilientUndefined)
        tmpl = env.from_string("rows={% for r in s.rows %}{{ r }}{% endfor %}|n={{ s.rows | length }}|x={{ s.count }}")
        # `s` itself is undefined → every access degrades to blank/zero.
        out = tmpl.render()
        assert out == "rows=|n=0|x="


# ── module-level filter registry wiring ──────────────────────────────


class TestEnvironmentWiring:
    def test_custom_filters_registered_on_env(self) -> None:
        d = DashboardServer()
        assert d._env.filters["human_bytes"] is _human_bytes
        assert d._env.filters["human_age"] is _human_age
        assert d._env.filters["short_id"] is _short_id

    def test_uses_resilient_undefined(self) -> None:
        d = DashboardServer()
        assert d._env.undefined is _ResilientUndefined

    def test_render_index_records_last_render_ms(self) -> None:
        # The self-stats counter starts at 0 and is set after a render
        # (used by the self-collector). No collectors → empty context.
        d = DashboardServer()
        assert d.last_render_ms() == 0.0
        d.set_collectors([])
        d.render_index()
        assert d.last_render_ms() >= 0.0
