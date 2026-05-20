"""`KnowledgeStore` contract — Strategy shared by every storage backend.

A blob lives at a *name* (e.g. ``knowledge:acme``,
``memory:reviewer-iklobato``, ``lessons:python-typing``) and has
markdown content. The name's prefix is used purely by convention to
group blobs into categories — the store treats the whole name as an
opaque identifier.

Concrete backends implement the four-verb contract: put / get / list /
delete. The base is `abc.ABC` (was `Protocol` before) so missing
methods on a subclass surface at construct time, not call time."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional


@dataclass
class KnowledgeRef:
    """One blob, as returned by `list`."""
    name: str
    category: str = ""
    byte_count: int = 0
    updated_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def category_of(blob_name: str) -> str:
        """Convention: text before the first colon is the category. A
        name without a colon belongs to the empty category."""
        head, sep, _ = blob_name.partition(":")
        return head if sep else ""


class KnowledgeStore(ABC):
    """Strategy contract. Each backend ships with a unique `name` that
    the registry indexes."""

    name: ClassVar[str] = ""

    @abstractmethod
    def put(
        self,
        blob_name: str,
        content: str,
        *,
        category: str = "",
    ) -> KnowledgeRef:
        """Create or update a blob. Returns the post-write reference."""

    @abstractmethod
    def get(self, blob_name: str) -> Optional[str]:
        """Return the markdown content, or None if the blob doesn't exist."""

    @abstractmethod
    def list(self, *, prefix: str = "") -> List[KnowledgeRef]:
        """Enumerate stored blobs. `prefix` matches against the start of
        the name — useful for filtering by category (e.g. `lessons:`)."""

    @abstractmethod
    def delete(self, blob_name: str) -> bool:
        """Return True if a row was removed, False if no such name."""


