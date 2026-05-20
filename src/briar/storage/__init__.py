"""KnowledgeStore — local file backend only.

Adding a new backend (sqlite, S3, …) is one file in this package + one
entry in `KnowledgeStoreRegistry.STORES`."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Type

from briar.errors import CliError
from briar.storage.base import KnowledgeRef, KnowledgeStore
from briar.storage.file import StoreFile


_DEFAULT_FILE_ROOT = Path("./knowledge")


class KnowledgeStoreRegistry:
    """Factory for the configured backends. Static-only."""

    STORES: Dict[str, Type[KnowledgeStore]] = {"file": StoreFile}

    @classmethod
    def names(cls) -> List[str]:
        return list(cls.STORES.keys())

    @classmethod
    def build(cls, name: str, file_root: Path = _DEFAULT_FILE_ROOT) -> KnowledgeStore:
        store_cls = cls.STORES.get(name)
        if store_cls is None:
            raise CliError(f"unknown knowledge store {name!r}; known: {', '.join(cls.names())}")
        if store_cls is StoreFile:
            return StoreFile(file_root)
        return store_cls()


# Module-level surface kept stable.
KNOWLEDGE_STORE_NAMES = tuple(KnowledgeStoreRegistry.names())
make_store = KnowledgeStoreRegistry.build


__all__ = [
    "KnowledgeStore",
    "KnowledgeRef",
    "KnowledgeStoreRegistry",
    "KNOWLEDGE_STORE_NAMES",
    "make_store",
]
