"""KnowledgeStore registry + factory.

Two backends today:
  - ``file``       — local `./knowledge/<category>/<name>.md`
  - ``briar-api``  — `Source(kind="static")` in the workspace,
                     visible to server-side agents

Adding a new backend (sqlite, S3, Postgres, …) is one file in this
package + one entry in `make_store`'s dispatch table."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from briar.errors import CliError
from briar.http import ApiClient
from briar.storage.base import KnowledgeRef, KnowledgeStore, category_of
from briar.storage.briar_api import StoreBriarApi
from briar.storage.file import StoreFile


# Names registered to the CLI's `--storage` flag.
KNOWLEDGE_STORE_NAMES = ("file", "briar-api")


def make_store(
    name: str,
    *,
    client: Optional[ApiClient] = None,
    file_root: Optional[Path] = None,
) -> KnowledgeStore:
    """Build the chosen backend.

    Dispatch-table style — no `elif` chain. Adding a new backend is one
    entry above + one case here."""
    if name == "file":
        return StoreFile(file_root or Path("./knowledge"))
    if name == "briar-api":
        if client is None:
            raise CliError(
                "store 'briar-api' requires an ApiClient — make sure "
                "you're logged in (`briar login`)"
            )
        return StoreBriarApi(client)
    raise CliError(
        f"unknown knowledge store {name!r}; "
        f"known: {', '.join(KNOWLEDGE_STORE_NAMES)}"
    )


__all__ = [
    "KnowledgeStore", "KnowledgeRef", "KNOWLEDGE_STORE_NAMES",
    "category_of", "make_store",
]
