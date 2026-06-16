"""Tool catalog for briar's MCP server.

Read tools have no `confirm` parameter and no side effects. Mutating/expensive
tools take ``confirm: bool = False`` — false returns a dry-run preview, true
performs the action — by mapping the flag onto `GateMode` and returning the
`GateResult` as a dict. The actual work lives in `briar.service`; this module
only adapts it to the MCP tool surface.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from briar import __version__
from briar.service import GateMode
from briar.service import extract as extract_service
from briar.service import knowledge as knowledge_service
from briar.service import runbook as runbook_service


def register_tools(mcp: Any, ctx: Any) -> None:
    """Register every briar tool on the FastMCP instance, closing over `ctx`."""

    def _runbook_path() -> str:
        if not ctx.runbook_path:
            raise ValueError("no runbook configured — start the server with `--runbook <path>` to use config tools")
        return str(ctx.runbook_path)

    # ── meta (read) ────────────────────────────────────────────────────
    @mcp.tool()
    def version() -> str:
        """Return the installed briar-cli version."""
        return str(__version__)

    # ── knowledge (reads) ──────────────────────────────────────────────
    @mcp.tool()
    def knowledge_list(prefix: str = "") -> List[Dict[str, Any]]:
        """List stored knowledge blobs, optionally filtered by name prefix."""
        return knowledge_service.list_blobs(store=ctx.store, root=ctx.root, prefix=prefix)

    @mcp.tool()
    def knowledge_get(blob_name: str) -> Optional[str]:
        """Return a knowledge blob's markdown body, or null if it doesn't exist."""
        return knowledge_service.get_blob(blob_name=blob_name, store=ctx.store, root=ctx.root)

    @mcp.tool()
    def knowledge_categories() -> List[Dict[str, Any]]:
        """List distinct knowledge categories and how many blobs each holds."""
        return knowledge_service.categories(store=ctx.store, root=ctx.root)

    # ── knowledge (gated) ──────────────────────────────────────────────
    @mcp.tool()
    def knowledge_put(blob_name: str, content: str, category: str = "", confirm: bool = False) -> Dict[str, Any]:
        """Create or update a knowledge blob. Dry-run unless confirm=true."""
        return knowledge_service.put_blob(
            blob_name=blob_name,
            content=content,
            category=category,
            store=ctx.store,
            root=ctx.root,
            gate=GateMode.from_confirm(confirm),
        ).as_dict()

    @mcp.tool()
    def knowledge_delete(blob_name: str, confirm: bool = False) -> Dict[str, Any]:
        """Delete a knowledge blob. Dry-run unless confirm=true."""
        return knowledge_service.delete_blob(
            blob_name=blob_name,
            store=ctx.store,
            root=ctx.root,
            gate=GateMode.from_confirm(confirm),
        ).as_dict()

    # ── runbook config (reads) ─────────────────────────────────────────
    @mcp.tool()
    def runbook_get() -> Dict[str, Any]:
        """Return the configured runbook as JSON (secrets are env-var names only)."""
        return runbook_service.to_dict(runbook_service.load(_runbook_path()))

    @mcp.tool()
    def runbook_validate() -> Dict[str, Any]:
        """Parse and schema-check the configured runbook; report the verdict."""
        return runbook_service.validate(_runbook_path())

    # ── runbook config (gated) ─────────────────────────────────────────
    @mcp.tool()
    def mcp_server_set_enabled(company: str, handle: str, enabled: bool, confirm: bool = False) -> Dict[str, Any]:
        """Enable or disable an MCP server in a company's runbook. Dry-run unless confirm=true."""
        return runbook_service.set_mcp_enabled(
            _runbook_path(),
            company=company,
            handle=handle,
            enabled=enabled,
            gate=GateMode.from_confirm(confirm),
        ).as_dict()

    # ── extraction (gated) ─────────────────────────────────────────────
    @mcp.tool()
    def extract_run(
        company: str,
        include: Optional[List[str]] = None,
        blob_name: str = "",
        out_json: str = "",
        confirm: bool = False,
    ) -> Dict[str, Any]:
        """Run knowledge extractors and write the blob. Dry-run (lists what would
        run, calls nothing) unless confirm=true."""
        return extract_service.run_extract(
            company=company,
            include=include,
            storage=ctx.store,
            blob_name=blob_name,
            root=ctx.root,
            out_json=out_json,
            gate=GateMode.from_confirm(confirm),
        ).as_dict()
