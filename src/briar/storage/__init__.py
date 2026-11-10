"""KnowledgeStore — pluggable backends.

One backend since 2.0 (decision C11): `file`, local markdown files at
`./knowledge/...`. The 1.x `postgres` backend was removed.

Adding a new backend is one file in this package + one entry in
`KnowledgeStoreRegistry.STORES` — the new class implements
`from_binding`, the registry stays closed."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Type

from briar.errors import CliError
from briar.storage.base import KnowledgeRef, KnowledgeStore, StoreBinding
from briar.storage.file import StoreFile

_DEFAULT_FILE_ROOT = Path("./knowledge")


class KnowledgeStoreRegistry:
    """Factory for the configured backends. Static-only.

    Construction goes through each backend's ``from_binding`` classmethod
    so the registry contains no backend-specific knowledge. Adding S3
    (etc.) is one ``STORES`` entry + one ``from_binding`` implementation
    — no edits here."""

    STORES: Dict[str, Type[KnowledgeStore]] = {"file": StoreFile}

    @classmethod
    def names(cls) -> List[str]:
        return list(cls.STORES.keys())

    @classmethod
    def build(
        cls,
        name: str,
        file_root: Path = _DEFAULT_FILE_ROOT,
        *,
        binding: Optional[StoreBinding] = None,
    ) -> KnowledgeStore:
        """Open a store. CLI-only callers pass `name` (+ optional
        `file_root`) and let the registry synthesize a binding; runbook
        executors pass a fully-populated ``binding``."""
        store_cls = cls.STORES.get(name)
        if store_cls is None:
            raise CliError(f"unknown knowledge store {name!r}; known: {', '.join(cls.names())}")
        resolved = binding if binding is not None else StoreBinding(store=name, root=str(file_root) if file_root else "")
        return store_cls.from_binding(resolved, default_root=file_root)


# Module-level surface kept stable.
make_store = KnowledgeStoreRegistry.build


__all__ = [
    "KnowledgeStore",
    "KnowledgeRef",
    "KnowledgeStoreRegistry",
    "StoreBinding",
    "make_store",
]
