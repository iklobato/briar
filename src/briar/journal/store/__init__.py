"""Journal stores — system-of-record backends.

One backend today (`file`). Postgres parallel slots in as a sibling
module + one registry entry, the same way `KnowledgeStore` does it."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Type

from briar.errors import CliError
from briar.journal.store.base import JournalRef, JournalStore, JournalStoreBinding
from briar.journal.store.file import FileJournalStore


_DEFAULT_FILE_ROOT = Path("./journal")


class JournalStoreRegistry:
    """Factory for journal store backends. Static-only."""

    STORES: Dict[str, Type[JournalStore]] = {"file": FileJournalStore}

    @classmethod
    def names(cls) -> List[str]:
        return list(cls.STORES.keys())

    @classmethod
    def build(
        cls,
        name: str,
        file_root: Path = _DEFAULT_FILE_ROOT,
        *,
        binding: Optional[JournalStoreBinding] = None,
    ) -> JournalStore:
        store_cls = cls.STORES.get(name)
        if store_cls is None:
            raise CliError(f"unknown journal store {name!r}; known: {', '.join(cls.names())}")
        resolved = binding if binding is not None else JournalStoreBinding(store=name, root=str(file_root) if file_root else "")
        return store_cls.from_binding(resolved, default_root=file_root)


JOURNAL_STORE_NAMES = tuple(JournalStoreRegistry.names())
make_journal_store = JournalStoreRegistry.build


__all__ = [
    "JournalStore",
    "JournalRef",
    "JournalStoreBinding",
    "JournalStoreRegistry",
    "JOURNAL_STORE_NAMES",
    "make_journal_store",
]
