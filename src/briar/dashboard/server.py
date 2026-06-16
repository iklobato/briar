"""HTTP server wrapper — stdlib http.server + Jinja2.

Read-only by construction: only GET (and HEAD) is registered. Any
other method falls through to `http.server`'s 501 default. Routes:
`/` renders the page, `/healthz` returns "ok"."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import List

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, select_autoescape

from briar.dashboard.collectors import Collector

_TEMPLATES_DIR = Path(__file__).parent / "templates"
log = logging.getLogger(__name__)


class _ResilientUndefined(ChainableUndefined):
    """Undefined that degrades to blanks instead of raising.

    A collector failure makes ``render_index`` set that section to
    ``{"_error": ...}``; the template then accesses fields that aren't
    there. With the default ``Undefined`` those accesses raise
    ``UndefinedError`` mid-render and 500 the WHOLE page — defeating the
    per-section isolation the dashboard promises ("a collector failure
    must not 500 the page"). Only 2 of ~24 sections hand-guard ``_error``,
    so the safety net belongs in the environment, not every section.

    ``ChainableUndefined`` already makes attribute/item access
    (``a.b.c``) return self instead of raising. We add the remaining
    operations the template performs on section fields: iteration
    (``{% for x in s.rows %}``), length (``s.rows | length``), numeric
    comparison (``s.x >= 0``), and string concatenation. ``| tojson`` is
    already guarded at the call sites with ``| default({})`` — and since
    this is an ``Undefined`` subclass, that filter still fires."""

    __slots__ = ()

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def __str__(self) -> str:
        return ""

    def __int__(self) -> int:
        return 0

    def __float__(self) -> float:
        return 0.0

    def __lt__(self, other) -> bool:
        return False

    __le__ = __gt__ = __ge__ = __lt__

    def __add__(self, other):
        return ""

    def __radd__(self, other):
        return other


# ─── Jinja filters ──────────────────────────────────────────────────
# Registered on the Environment in DashboardServer.__init__. Each one
# is a pure function; templates use them as `{{ value | filter_name }}`.
# Keeping these here (rather than spreading inline math across the
# template) is the canonical Jinja simplification.


def _human_bytes(value) -> str:
    """`3725` → `3.6 KB`. Robust against non-numeric / None inputs."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ""
    if n < 0:
        n = 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _human_age(value) -> str:
    """ISO-8601 timestamp → human chunk ('3m ago' / '2h ago' / '4d ago').
    Empty string when the timestamp is missing or unparseable."""
    if not value:
        return ""
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _short_id(value) -> str:
    """First 8 chars of a long id (session id, fingerprint). Templates
    use it so the full id stays available for click-through but the
    rendered cell stays tabular."""
    s = str(value or "")
    return s[:8]


def _parse_ts(value):
    """Parse any timestamp the collectors emit → aware UTC datetime, or None.

    Handles git ISO (`%cI`), raw log ISO (`2026-06-16T03:00:05`), and the
    `... UTC` display forms the schedule/system collectors produce."""
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M UTC"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _fmt_chunk(seconds: int) -> str:
    """Coarse duration → at most two units: '4h 12m', '3d', '45s'."""
    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def _reltime(value) -> str:
    """Timestamp → relative, human form: 'in 4h 12m', '5m ago', 'just now'.
    Unparseable values pass through unchanged; empty → ''."""
    ts = _parse_ts(value)
    if ts is None:
        return str(value or "")
    delta = int((datetime.now(timezone.utc) - ts).total_seconds())
    if abs(delta) < 45:
        return "just now"
    chunk = _fmt_chunk(abs(delta))
    return f"in {chunk}" if delta < 0 else f"{chunk} ago"


def _stamp(value) -> str:
    """Timestamp → compact absolute for tooltips / the render clock:
    'Jun 16 22:47 UTC'. Unparseable values pass through; empty → ''."""
    ts = _parse_ts(value)
    if ts is None:
        return str(value or "")
    return ts.strftime("%b %d %H:%M UTC")


_JINJA_FILTERS = {
    "human_bytes": _human_bytes,
    "human_age": _human_age,
    "short_id": _short_id,
    "reltime": _reltime,
    "stamp": _stamp,
}


class DashboardServer:
    """Renders one Jinja page; tracks per-process self-stats."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        self._collectors: List[Collector] = []
        self._host = host
        self._port = port
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
            # A failed collector's section is missing its fields; degrade
            # to blanks instead of 500ing the whole page mid-render.
            undefined=_ResilientUndefined,
        )
        # Register custom filters in one shot — the template uses them
        # as `{{ value | human_bytes }}` etc.
        self._env.filters.update(_JINJA_FILTERS)
        self._req_lock = Lock()
        self._request_count = 0
        self._last_render_ms = 0.0
        self.started_at = time.time()

    def set_collectors(self, collectors: List[Collector]) -> None:
        self._collectors = collectors
        log.debug("dashboard collectors set: %d registered", len(collectors))

    # ---- live counters used by the self-collector ---------------------

    def request_count(self) -> int:
        return self._request_count

    def last_render_ms(self) -> float:
        return self._last_render_ms

    # ---- main entry points --------------------------------------------

    def render_index(self) -> str:
        """Run every collector and render the template. Each collector
        failure is caught and logged with a traceback; the page renders
        without that section rather than 500ing."""
        started = time.monotonic()
        context = {}
        for c in self._collectors:
            try:
                context[c.name] = c.collect()
            except Exception:  # noqa: BLE001
                log.exception("collector %s.collect() raised; section will render empty", c.name)
                context[c.name] = {"_error": "collector failed; see scheduler.log"}
        html = self._env.get_template("index.html").render(**context)
        self._last_render_ms = (time.monotonic() - started) * 1000
        log.debug("render: %d collectors, %.1f ms, %d bytes", len(self._collectors), self._last_render_ms, len(html))
        return html

    def increment_requests(self) -> None:
        with self._req_lock:
            self._request_count += 1

    def serve(self) -> None:
        handler_cls = _build_handler(self)
        httpd = ThreadingHTTPServer((self._host, self._port), handler_cls)
        log.info("dashboard listening on http://%s:%d", self._host, self._port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            log.warning("dashboard shutting down (KeyboardInterrupt)")
            httpd.shutdown()


def _build_handler(dashboard: DashboardServer):
    """Closure that gives the request class a reference to the server."""

    class _Handler(BaseHTTPRequestHandler):
        server_version = "briar-dashboard/2.0"

        def do_GET(self) -> None:  # noqa: N802 — http.server interface
            dashboard.increment_requests()
            handlers = {
                "/": self._render_index,
                "/healthz": self._healthz,
            }
            handler = handlers.get(self.path.split("?", 1)[0])
            if handler is None:
                log.info("404 %s %s from %s", self.command, self.path, self.address_string())
                self._respond(HTTPStatus.NOT_FOUND, "not found", "text/plain")
                return
            try:
                handler()
            except Exception:  # noqa: BLE001
                log.exception("handler crashed for %s %s", self.command, self.path)
                self._respond(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error\n", "text/plain")

        def do_HEAD(self) -> None:  # noqa: N802
            self._head_only = True
            try:
                self.do_GET()
            finally:
                self._head_only = False

        def _render_index(self) -> None:
            body = dashboard.render_index()
            self._respond(HTTPStatus.OK, body, "text/html; charset=utf-8")

        def _healthz(self) -> None:
            self._respond(HTTPStatus.OK, "ok\n", "text/plain; charset=utf-8")

        def _respond(self, status: HTTPStatus, body: str, content_type: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            head_only = vars(self).get("_head_only", False)
            if not head_only:
                self.wfile.write(encoded)

        def log_message(self, fmt: str, *args) -> None:
            # When the request line itself fails to parse (scanner traffic,
            # malformed clients, raw TCP probes) the base class calls us
            # before `self.path` or `self.command` are populated. Falling
            # back to the formatted message means we still log a per-request
            # line without raising AttributeError mid-`send_error`.
            try:
                command = self.command
                path = self.path
            except AttributeError:
                log.info("%s malformed-request %s", self.address_string(), fmt % args if args else fmt)
                return
            log.info("%s %s %s", self.address_string(), command, path)

    return _Handler
