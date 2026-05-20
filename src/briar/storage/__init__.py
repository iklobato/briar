"""KnowledgeStore — local file backend only.

The old `briar-api` backend was removed when the CLI dropped its
remote-call surface. Adding a new backend (sqlite, S3, …) is one file
in this package + one entry in `make_store`'s dispatch table."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from briar.errors import CliError
from briar.storage.base import KnowledgeRef, KnowledgeStore, category_of
from briar.storage.file import StoreFile


KNOWLEDGE_STORE_NAMES = ("file",)


def make_store(
    name: str,
    *,
    file_root: Optional[Path] = None,
) -> KnowledgeStore:
    if name == "file":
        return StoreFile(file_root or Path("./knowledge"))
    raise CliError(
        f"unknown knowledge store {name!r}; "
        f"known: {', '.join(KNOWLEDGE_STORE_NAMES)}"
    )


__all__ = [
    "KnowledgeStore", "KnowledgeRef", "KNOWLEDGE_STORE_NAMES",
    "category_of", "make_store",
]
