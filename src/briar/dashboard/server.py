"""HTTP server wrapper — stdlib http.server + Jinja2.

Read-only by construction: only GET is registered. Any other method
returns 405. The single rendered route is `/`; `/healthz` is the only
other path (for liveness checks)."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from briar.dashboard.collectors import Collector, CollectorRegistry


_TEMPLATES_DIR = Path(__file__).parent / "templates"


class DashboardServer:
    """Single-use HTTP server that renders one Jinja page.

    Hold collectors + the Jinja environment as instance state; the
    request handler reads them via the server attribute set in `serve`."""

    def __init__(
        self,
        collectors: List[Collector],
        *,
        host: str = "0.0.0.0",
        port: int = 8080,
    ) -> None:
        self._collectors = collectors
        self._host = host
        self._port = port
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render_index(self) -> str:
        """Run every collector and render the template. Public so tests
        can call it without spinning up HTTP."""
        context = CollectorRegistry.collect_all(self._collectors)
        return self._env.get_template("index.html").render(**context)

    def serve(self) -> None:
        handler_cls = _build_handler(self)
        httpd = ThreadingHTTPServer((self._host, self._port), handler_cls)
        print(f"dashboard listening on http://{self._host}:{self._port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nshutting down")
            httpd.shutdown()


def _build_handler(dashboard: DashboardServer):
    """Closure that gives the request class a reference to the server."""

    class _Handler(BaseHTTPRequestHandler):
        server_version = "briar-dashboard/1.0"

        def do_GET(self) -> None:  # noqa: N802 — http.server interface
            handlers = {
                "/": self._render_index,
                "/healthz": self._healthz,
            }
            handler = handlers.get(self.path.split("?", 1)[0])
            if handler is None:
                self._respond(HTTPStatus.NOT_FOUND, "not found", "text/plain")
                return
            handler()

        def do_HEAD(self) -> None:  # noqa: N802
            # HEAD is just GET-without-body; reuse with a flag.
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
            self.end_headers()
            head_only = vars(self).get("_head_only", False)
            if not head_only:
                self.wfile.write(encoded)

        def log_message(self, fmt: str, *args) -> None:
            # Quieter default log line — one line per request.
            print(f"{self.address_string()} {self.command} {self.path} {args[1] if len(args) > 1 else ''}".strip())

    return _Handler
