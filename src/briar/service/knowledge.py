"""Knowledge-blob operations, presentation-free.

Wraps the `KnowledgeStore` four-verb contract so the CLI (`briar context`),
the MCP server, and the dashboard share one code path. Reads return plain
dicts/strings; mutations are gated (`put_blob`, `delete_blob`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from briar.service._gating import GateMode, GateResult
from briar.storage import KnowledgeRef, make_store


def _store(store: str, root: str):
    return make_store(store, file_root=Path(root))


def ref_to_dict(ref: KnowledgeRef) -> Dict[str, Any]:
    """Flatten a `KnowledgeRef` to a JSON-friendly dict (extras inlined)."""
    return {
        "name": ref.name,
        "category": ref.category,
        "byte_count": ref.byte_count,
        "updated_at": ref.updated_at,
        **ref.extra,
    }


def list_blobs(*, store: str = "file", root: str = "./knowledge", prefix: str = "") -> List[Dict[str, Any]]:
    return [ref_to_dict(r) for r in _store(store, root).list(prefix=prefix)]


def get_blob(*, blob_name: str, store: str = "file", root: str = "./knowledge") -> Optional[str]:
    """Return the blob body, or ``None`` when it does not exist (the store's
    empty-string sentinel is mapped to None so callers branch cleanly)."""
    body = _store(store, root).get(blob_name)
    return body or None


def categories(*, store: str = "file", root: str = "./knowledge") -> List[Dict[str, Any]]:
    seen: Dict[str, int] = {}
    for ref in _store(store, root).list():
        seen[ref.category] = seen.get(ref.category, 0) + 1
    return [{"category": cat or "(none)", "blob_count": n} for cat, n in sorted(seen.items())]


def put_blob(
    *,
    blob_name: str,
    content: str,
    category: str = "",
    store: str = "file",
    root: str = "./knowledge",
    gate: GateMode = GateMode.EXECUTE,
) -> GateResult:
    cat = category or KnowledgeRef.category_of(blob_name)
    if gate is GateMode.DRY_RUN:
        return GateResult.previewed(f"would write {len(content)} bytes to {blob_name!r} (category={cat or '(none)'}, store={store})")
    ref = _store(store, root).put(blob_name, content, category=category)
    return GateResult.performed(f"wrote {ref.byte_count} bytes to {ref.name!r} (store={store})", {"ref": ref_to_dict(ref)})


def delete_blob(
    *,
    blob_name: str,
    store: str = "file",
    root: str = "./knowledge",
    gate: GateMode = GateMode.EXECUTE,
) -> GateResult:
    if gate is GateMode.DRY_RUN:
        exists = bool(_store(store, root).get(blob_name))
        return GateResult.previewed(f"would delete {blob_name!r} from store={store} ({'exists' if exists else 'not found'})")
    removed = _store(store, root).delete(blob_name)
    return GateResult.performed(f"{'deleted' if removed else 'not found'} {blob_name}", {"removed": removed})
