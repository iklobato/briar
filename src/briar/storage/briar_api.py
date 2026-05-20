"""Briar-backend knowledge store — each blob is a `Source(kind="static")`.

A blob's *name* lives in `Source.name` and its content in
`Source.config["content"]`. Categories are stored under
`Source.config["category"]`. This is the path that makes the blob
visible to server-side agents — the orchestrator gathers any
`source_key`-bound static source into `task.context` on every run,
identical to how it gathers GitHub or AWS sources.

The connector backing this on the server is
`apps/sources/connectors/static.py`."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from briar.errors import ApiError
from briar.http import ApiClient
from briar.storage.base import KnowledgeRef, category_of


_KIND = "static"


def _row_to_ref(row: Dict[str, Any]) -> KnowledgeRef:
    config = row.get("config") or {}
    content = config.get("content") or ""
    return KnowledgeRef(
        name=row.get("name") or "",
        category=config.get("category") or category_of(row.get("name") or ""),
        byte_count=len(content),
        updated_at=row.get("updated_at") or "",
        extra={"id": row.get("id")},
    )


class StoreBriarApi:
    name = "briar-api"

    def __init__(self, client: ApiClient) -> None:
        self._client = client

    # ---- helpers ---------------------------------------------------------

    def _find(self, blob_name: str) -> Optional[Dict[str, Any]]:
        rows = self._client.list_all(
            "/api/v1/sources/", query={"kind": _KIND},
        )
        for r in rows:
            if r.get("name") == blob_name and r.get("kind") == _KIND:
                return r
        return None

    # ---- KnowledgeStore impl --------------------------------------------

    def put(
        self,
        blob_name: str,
        content: str,
        *,
        category: str = "",
    ) -> KnowledgeRef:
        body = {
            "name": blob_name,
            "kind": _KIND,
            "config": {
                "content": content,
                "category": category or category_of(blob_name),
            },
            "cache_policy": {},
            "is_enabled": True,
        }
        existing = self._find(blob_name)
        if existing is None:
            row = self._client.request("POST", "/api/v1/sources/", body)
            return _row_to_ref(row)
        row = self._client.request(
            "PATCH",
            f"/api/v1/sources/{existing['id']}/",
            body,
        )
        return _row_to_ref(row)

    def get(self, blob_name: str) -> Optional[str]:
        row = self._find(blob_name)
        if row is None:
            return None
        config = row.get("config") or {}
        content = config.get("content")
        return content if isinstance(content, str) else None

    def list(self, *, prefix: str = "") -> List[KnowledgeRef]:
        rows = self._client.list_all(
            "/api/v1/sources/", query={"kind": _KIND},
        )
        out: List[KnowledgeRef] = []
        for r in rows:
            if r.get("kind") != _KIND:
                continue
            name = r.get("name") or ""
            if prefix and not name.startswith(prefix):
                continue
            out.append(_row_to_ref(r))
        return out

    def delete(self, blob_name: str) -> bool:
        row = self._find(blob_name)
        if row is None:
            return False
        try:
            self._client.request("DELETE", f"/api/v1/sources/{row['id']}/")
        except ApiError as exc:
            # 409 (FK-protected) bubbles up to the caller; everything else
            # we treat as a hard failure too.
            raise exc
        return True
