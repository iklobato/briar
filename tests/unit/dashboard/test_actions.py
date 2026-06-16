"""Read-write dashboard: ActionRouter gating/CSRF + server POST wiring."""

from __future__ import annotations

from http import HTTPStatus

from briar.dashboard.actions import ActionRouter
from briar.dashboard.server import DashboardServer
from briar.iac.runbook import RunbookFile, load_runbook_file, save_runbook_file
from briar.service import knowledge as ks

_TOK = "csrf-token"


def _router(tmp_path, runbook=None):
    captured = []

    def render(name, **ctx):
        captured.append((name, ctx))
        return f"<{name}>"

    router = ActionRouter(store="file", root=str(tmp_path / "kn"), csrf_token=_TOK, render=render, runbook_path=runbook)
    return router, captured


# ── CSRF + routing ───────────────────────────────────────────────────


def test_bad_csrf_is_forbidden_and_no_op(tmp_path) -> None:
    router, cap = _router(tmp_path)
    res = router.handle("/action/knowledge/put", {"csrf": "wrong", "blob_name": "knowledge:a", "content": "x"})
    assert res.status == HTTPStatus.FORBIDDEN
    assert cap[-1][0] == "_action.html" and cap[-1][1]["ok"] is False
    assert ks.get_blob(blob_name="knowledge:a", root=str(tmp_path / "kn")) is None


def test_unknown_action_is_404(tmp_path) -> None:
    router, _ = _router(tmp_path)
    res = router.handle("/action/nope", {"csrf": _TOK})
    assert res.status == HTTPStatus.NOT_FOUND


def test_missing_required_field_is_400(tmp_path) -> None:
    router, _ = _router(tmp_path)
    res = router.handle("/action/knowledge/put", {"csrf": _TOK, "content": "x"})
    assert res.status == HTTPStatus.BAD_REQUEST


# ── gated knowledge write ──────────────────────────────────────────────


def test_put_without_confirm_renders_confirm_page_no_write(tmp_path) -> None:
    router, cap = _router(tmp_path)
    res = router.handle("/action/knowledge/put", {"csrf": _TOK, "blob_name": "knowledge:a", "content": "hi"})
    assert res.status == HTTPStatus.OK
    name, ctx = cap[-1]
    assert name == "_confirm.html"
    # The confirm page re-posts the original fields + the action path.
    assert ctx["fields"] == {"blob_name": "knowledge:a", "content": "hi"}
    assert ctx["action"] == "/action/knowledge/put"
    assert ctx["csrf"] == _TOK
    # No write happened on the dry-run.
    assert ks.get_blob(blob_name="knowledge:a", root=str(tmp_path / "kn")) is None


def test_put_with_confirm_executes(tmp_path) -> None:
    router, cap = _router(tmp_path)
    res = router.handle("/action/knowledge/put", {"csrf": _TOK, "confirm": "1", "blob_name": "knowledge:a", "content": "hi"})
    assert res.status == HTTPStatus.OK
    assert cap[-1][0] == "_action.html" and cap[-1][1]["ok"] is True
    assert ks.get_blob(blob_name="knowledge:a", root=str(tmp_path / "kn")) == "hi"


def test_delete_with_confirm_removes(tmp_path) -> None:
    root = str(tmp_path / "kn")
    ks.put_blob(blob_name="knowledge:a", content="x", root=root)
    router, _ = _router(tmp_path)
    router.handle("/action/knowledge/delete", {"csrf": _TOK, "confirm": "1", "blob_name": "knowledge:a"})
    assert ks.get_blob(blob_name="knowledge:a", root=root) is None


# ── runbook config action ──────────────────────────────────────────────


def test_mcp_toggle_without_runbook_is_400(tmp_path) -> None:
    router, _ = _router(tmp_path, runbook=None)
    res = router.handle("/action/mcp/toggle", {"csrf": _TOK, "confirm": "1", "company": "acme", "handle": "github", "enabled": "0"})
    assert res.status == HTTPStatus.BAD_REQUEST


def test_mcp_toggle_with_confirm_persists(tmp_path) -> None:
    rb_path = tmp_path / "rb.yaml"
    save_runbook_file(rb_path, RunbookFile.model_validate({"companies": {"acme": {"mcp": {"github": {"command": "docker", "enabled": True}}}}}))
    router, _ = _router(tmp_path, runbook=str(rb_path))
    router.handle("/action/mcp/toggle", {"csrf": _TOK, "confirm": "1", "company": "acme", "handle": "github", "enabled": "0"})
    assert load_runbook_file(rb_path).companies["acme"].mcp["github"].enabled is False


# ── server POST wiring ─────────────────────────────────────────────────


def test_server_read_only_by_default_blocks_writes() -> None:
    assert DashboardServer().writes_enabled is False


def test_server_with_router_enables_writes_and_routes(tmp_path) -> None:
    server = DashboardServer(read_only=False)
    router, _ = _router(tmp_path)
    server.set_action_router(router)
    assert server.writes_enabled is True
    status, html = server.handle_post("/action/knowledge/put", b"csrf=csrf-token&blob_name=knowledge:a&content=hi")
    assert status == HTTPStatus.OK
    assert html == "<_confirm.html>"  # dry-run → confirm page


def test_do_post_through_handler_routes_and_responds(tmp_path) -> None:
    import io

    from briar.dashboard.server import _build_handler

    class _FakeSocket:
        def __init__(self, request_bytes: bytes) -> None:
            self._rfile = io.BytesIO(request_bytes)
            self.wfile = io.BytesIO()

        def makefile(self, mode: str, *a, **k):
            return self.wfile if "w" in mode else self._rfile

        def sendall(self, data):
            self.wfile.write(data)

    server = DashboardServer(read_only=False)
    server.set_action_router(_router(tmp_path)[0])
    body = b"csrf=csrf-token&blob_name=knowledge:a&content=hi"
    req = (
        b"POST /action/knowledge/put HTTP/1.1\r\nHost: x\r\n"
        b"Content-Type: application/x-www-form-urlencoded\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    sock = _FakeSocket(req)
    _build_handler(server)(sock, ("127.0.0.1", 1), None)
    resp = sock.wfile.getvalue()
    assert b"200 OK" in resp.split(b"\r\n", 1)[0]
    assert resp.endswith(b"<_confirm.html>")  # dry-run confirm page


def test_control_section_renders_only_when_writes_enabled(tmp_path) -> None:
    rw = DashboardServer(read_only=False)
    rw.set_action_router(_router(tmp_path)[0])
    rw.set_collectors([])
    assert "control panel" in rw.render_index()

    ro = DashboardServer()  # default read-only, no router
    ro.set_collectors([])
    assert "control panel" not in ro.render_index()
