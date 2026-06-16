"""Read-write action router for the dashboard.

Maps ``POST /action/*`` requests onto `briar.service` operations, applying the
same dry-run/confirm gate the MCP server and `briar chat` use, plus a per-process
CSRF token so a drive-by page can't trigger a mutation.

Flow for a gated action:
  1. The control-panel form posts without ``confirm`` → the service runs in
     DRY_RUN, and the router returns a **confirm page** echoing the intended
     change as hidden fields plus a "Confirm" button (which re-posts with
     ``confirm=1``).
  2. The confirm page posts with ``confirm=1`` → the service EXECUTEs and the
     router returns a result page.

Every action validates the CSRF token first; routing/lookup happens after.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Callable, Dict, Optional

from briar.errors import CliError
from briar.service import GateMode, GateResult
from briar.service import extract as extract_service
from briar.service import knowledge as knowledge_service
from briar.service import runbook as runbook_service

_RESERVED = {"csrf", "confirm"}


@dataclass
class ActionResult:
    status: HTTPStatus
    html: str


@dataclass
class ActionRouter:
    """Dispatches POSTed forms to gated service calls. `render` is the
    server's Jinja `get_template(name).render(**ctx)` shim."""

    store: str
    root: str
    csrf_token: str
    render: Callable[..., str]
    runbook_path: Optional[str] = None
    _routes: Dict[str, Callable[[Dict[str, str]], "ActionResult"]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._routes = {
            "/action/knowledge/put": self._knowledge_put,
            "/action/knowledge/delete": self._knowledge_delete,
            "/action/extract/run": self._extract_run,
            "/action/mcp/toggle": self._mcp_toggle,
        }

    def handle(self, path: str, form: Dict[str, str]) -> ActionResult:
        if not hmac.compare_digest(form.get("csrf", ""), self.csrf_token):
            return self._page(HTTPStatus.FORBIDDEN, "Forbidden", "Invalid or missing CSRF token.", ok=False)
        handler = self._routes.get(path)
        if handler is None:
            return self._page(HTTPStatus.NOT_FOUND, "Unknown action", f"No action at {path}.", ok=False)
        try:
            return handler(form)
        except CliError as exc:
            return self._page(HTTPStatus.BAD_REQUEST, "Error", str(exc), ok=False)

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _gate(form: Dict[str, str]) -> GateMode:
        return GateMode.from_confirm(form.get("confirm") == "1")

    @staticmethod
    def _require(form: Dict[str, str], key: str) -> str:
        value = (form.get(key) or "").strip()
        if not value:
            raise CliError(f"missing required field {key!r}")
        return value

    def _page(self, status: HTTPStatus, title: str, message: str, *, ok: bool) -> ActionResult:
        return ActionResult(status, self.render("_action.html", title=title, message=message, ok=ok))

    def _outcome(self, path: str, form: Dict[str, str], result: GateResult, label: str) -> ActionResult:
        if result.executed:
            return self._page(HTTPStatus.OK, f"{label} — done", result.summary, ok=True)
        # Dry-run: render the confirm page that re-posts the same fields + confirm=1.
        fields = {k: v for k, v in form.items() if k not in _RESERVED}
        html = self.render("_confirm.html", action=path, summary=result.summary, fields=fields, csrf=self.csrf_token, label=label)
        return ActionResult(HTTPStatus.OK, html)

    # ── actions ─────────────────────────────────────────────────────────

    def _knowledge_put(self, form: Dict[str, str]) -> ActionResult:
        result = knowledge_service.put_blob(
            blob_name=self._require(form, "blob_name"),
            content=form.get("content", ""),
            category=form.get("category", ""),
            store=self.store,
            root=self.root,
            gate=self._gate(form),
        )
        return self._outcome("/action/knowledge/put", form, result, "Write blob")

    def _knowledge_delete(self, form: Dict[str, str]) -> ActionResult:
        result = knowledge_service.delete_blob(
            blob_name=self._require(form, "blob_name"),
            store=self.store,
            root=self.root,
            gate=self._gate(form),
        )
        return self._outcome("/action/knowledge/delete", form, result, "Delete blob")

    def _extract_run(self, form: Dict[str, str]) -> ActionResult:
        include = [s.strip() for s in (form.get("include") or "").split(",") if s.strip()]
        result = extract_service.run_extract(
            company=self._require(form, "company"),
            include=include or None,
            storage=self.store,
            blob_name=form.get("blob_name", ""),
            root=self.root,
            gate=self._gate(form),
        )
        return self._outcome("/action/extract/run", form, result, "Run extractors")

    def _mcp_toggle(self, form: Dict[str, str]) -> ActionResult:
        if not self.runbook_path:
            raise CliError("no runbook configured — start the dashboard with --runbook to edit MCP servers")
        enabled = (form.get("enabled") or "").strip().lower() in {"1", "true", "on", "yes"}
        result = runbook_service.set_mcp_enabled(
            self.runbook_path,
            company=self._require(form, "company"),
            handle=self._require(form, "handle"),
            enabled=enabled,
            gate=self._gate(form),
        )
        return self._outcome("/action/mcp/toggle", form, result, "Toggle MCP server")
