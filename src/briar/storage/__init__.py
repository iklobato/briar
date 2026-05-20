"""KnowledgeStore — pluggable backends.

Two backends today:
- `file`     — local markdown files at `./knowledge/...` (laptop dev)
- `postgres` — DO managed Postgres (production droplet); DSN comes
               from `BRIAR_DATABASE_URL` env var

Adding a new backend is one file in this package + one entry in
`KnowledgeStoreRegistry.STORES`."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Type

from briar.env_vars import CredEnv
from briar.errors import CliError
from briar.storage.base import KnowledgeRef, KnowledgeStore
from briar.storage.file import StoreFile
from briar.storage.postgres import StorePostgres


_DEFAULT_FILE_ROOT = Path("./knowledge")


class KnowledgeStoreRegistry:
    """Factory for the configured backends. Static-only."""

    STORES: Dict[str, Type[KnowledgeStore]] = {"file": StoreFile, "postgres": StorePostgres}

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
        if store_cls is StorePostgres:
            dsn = CredEnv.BRIAR_DATABASE_URL.read()
            if not dsn:
                raise CliError("store 'postgres' requires the BRIAR_DATABASE_URL env var")
            return StorePostgres(dsn)
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
