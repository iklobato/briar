"""Local-file knowledge store — one markdown file per blob.

Default layout: ``./knowledge/<category>/<rest-of-name>.md``. A blob
named ``knowledge:acme`` becomes ``./knowledge/knowledge/acme.md``;
a name without a colon goes to ``./knowledge/<name>.md``."""

from __future__ import annotations

from pathlib import Path
from typing import List

from briar.storage.base import KnowledgeRef, KnowledgeStore


class StoreFile(KnowledgeStore):
    name = "file"

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, blob_name: str) -> Path:
        """Three name shapes are supported:
        - `<category>:<rest>`      → `<root>/<category>/<rest>.md`
        - `<bare>`                 → `<root>/<bare>.md`
        - path-like (`/` or `.md`) → use the path verbatim."""
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
        try:
            rel = path.relative_to(self._root)
        except ValueError:
            return str(path)
        parts = rel.with_suffix("").parts
        if len(parts) == 1:
            return parts[0]
        category, *rest = parts
        return f"{category}:{'/'.join(rest)}"

    def put(self, blob_name: str, content: str, category: str = "") -> KnowledgeRef:
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

    def get(self, blob_name: str) -> str:
        path = self._path_for(blob_name)
        if not path.exists():
            return ""
        return path.read_text()

    def list(self, prefix: str = "") -> List[KnowledgeRef]:
        out: List[KnowledgeRef] = []
        for path in sorted(self._root.rglob("*.md")):
            blob_name = self._name_for(path)
            if prefix and not blob_name.startswith(prefix):
                continue
            stat = path.stat()
            out.append(
                KnowledgeRef(
                    name=blob_name,
                    category=KnowledgeRef.category_of(blob_name),
                    byte_count=stat.st_size,
                    updated_at=str(stat.st_mtime),
                    extra={"path": str(path)},
                )
            )
        return out

    def delete(self, blob_name: str) -> bool:
        path = self._path_for(blob_name)
        if not path.exists():
            return False
        path.unlink()
        parent = path.parent
        if parent != self._root and not any(parent.iterdir()):
            parent.rmdir()
        return True
