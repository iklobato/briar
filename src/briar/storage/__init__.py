"""KnowledgeStore — local file backend only.

The old `briar-api` backend was removed when the CLI dropped its
remote-call surface. Adding a new backend (sqlite, S3, …) is one file
in this package + one entry in `KnowledgeStoreRegistry.STORES`."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Type

from briar.errors import CliError
from briar.storage.base import KnowledgeRef, KnowledgeStore
from briar.storage.file import StoreFile


class KnowledgeStoreRegistry:
    """Factory for the configured backends. Static-only — no instance
    state worth keeping."""

    STORES: Dict[str, Type[KnowledgeStore]] = {
        "file": StoreFile,
    }

    @classmethod
    def names(cls) -> List[str]:
        return list(cls.STORES.keys())

    @classmethod
    def build(
        cls,
        name: str,
        *,
        file_root: Optional[Path] = None,
    ) -> KnowledgeStore:
        store_cls = cls.STORES.get(name)
        if store_cls is None:
            raise CliError(
                f"unknown knowledge store {name!r}; "
                f"known: {', '.join(cls.names())}"
            )
        if store_cls is StoreFile:
            return StoreFile(file_root or Path("./knowledge"))
        return store_cls()


# Module-level surface kept stable.
KNOWLEDGE_STORE_NAMES = tuple(KnowledgeStoreRegistry.names())
make_store = KnowledgeStoreRegistry.build


__all__ = [
    "KnowledgeStore", "KnowledgeRef", "KnowledgeStoreRegistry",
    "KNOWLEDGE_STORE_NAMES", "make_store",
]
