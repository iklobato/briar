"""Local-file knowledge store — one markdown file per blob.

Default layout: ``./knowledge/<category>/<rest-of-name>.md``. A blob
named ``knowledge:acme`` becomes ``./knowledge/knowledge/acme.md``;
a name without a colon goes to ``./knowledge/<name>.md``. This is the
zero-dependency local-only backend; agents running on the server can't
see it (use the `briar-api` backend for that)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from briar.storage.base import KnowledgeRef, KnowledgeStore


class StoreFile(KnowledgeStore):
    name = "file"

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    # ---- path resolution ------------------------------------------------

    def _path_for(self, blob_name: str) -> Path:
        """Three name shapes are supported:
          - `<category>:<rest>`      → `<root>/<category>/<rest>.md`
          - `<bare>`                 → `<root>/<bare>.md`
          - path-like (`/` or `.md`) → use the path verbatim
            (this is what the legacy `knowledge_file:` shortcut hits)."""
        if "/" in blob_name or blob_name.endswith(".md"):
            path = Path(blob_name)
            if not path.suffix:
                path = path.with_suffix(".md")
            return path
        category, sep, rest = blob_name.partition(":")
        if not sep:
            return self._root / f"{blob_name}.md"
        return self._root / category / f"{rest}.md"

    def _name_for(self, path: Path) -> str:
        """Inverse of `_path_for` — used by `list()`."""
        try:
            rel = path.relative_to(self._root)
        except ValueError:
            return str(path)
        parts = rel.with_suffix("").parts
        if len(parts) == 1:
            return parts[0]
        category, *rest = parts
        return f"{category}:{'/'.join(rest)}"

    # ---- KnowledgeStore impl --------------------------------------------

    def put(
        self,
        blob_name: str,
        content: str,
        *,
        category: str = "",
    ) -> KnowledgeRef:
        path = self._path_for(blob_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return KnowledgeRef(
            name=blob_name,
            category=category or KnowledgeRef.category_of(blob_name),
            byte_count=len(content),
            updated_at="",
            extra={"path": str(path)},
        )

    def get(self, blob_name: str) -> Optional[str]:
        path = self._path_for(blob_name)
        if not path.exists():
            return None
        return path.read_text()

    def list(self, *, prefix: str = "") -> List[KnowledgeRef]:
        out: List[KnowledgeRef] = []
        for path in sorted(self._root.rglob("*.md")):
            blob_name = self._name_for(path)
            if prefix and not blob_name.startswith(prefix):
                continue
            stat = path.stat()
            out.append(KnowledgeRef(
                name=blob_name,
                category=KnowledgeRef.category_of(blob_name),
                byte_count=stat.st_size,
                updated_at=str(stat.st_mtime),
                extra={"path": str(path)},
            ))
        return out

    def delete(self, blob_name: str) -> bool:
        path = self._path_for(blob_name)
        if not path.exists():
            return False
        path.unlink()
        # Best-effort: also rmdir the (now-empty) category directory.
        parent = path.parent
        if parent != self._root and not any(parent.iterdir()):
            parent.rmdir()
        return True
