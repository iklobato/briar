"""HTTP server wrapper — stdlib http.server + Jinja2.

Read-only by construction: only GET (and HEAD) is registered. Any
other method falls through to `http.server`'s 501 default. Routes:
`/` renders the page, `/healthz` returns "ok"."""

from __future__ import annotations

import logging
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import List

from jinja2 import Environment, FileSystemLoader, select_autoescape

from briar.dashboard.collectors import Collector, CollectorRegistry


_TEMPLATES_DIR = Path(__file__).parent / "templates"
log = logging.getLogger(__name__)


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
        )
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
            log.info("%s %s %s", self.address_string(), self.command, self.path)

    return _Handler
