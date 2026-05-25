"""`KnowledgeStore` contract — Strategy shared by every storage backend.

A blob lives at a *name* (e.g. ``knowledge:acme``,
``memory:reviewer-iklobato``, ``lessons:python-typing``) and has
markdown content. The name's prefix is used purely by convention to
group blobs into categories — the store treats the whole name as an
opaque identifier.

Concrete backends implement the four-verb contract: put / get / list /
delete. The base is `abc.ABC` so missing methods on a subclass surface
at construct time, not call time."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterable, List, Mapping, Optional


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
        head, sep, _ = blob_name.partition(":")
        return head if sep else ""


@dataclass(frozen=True)
class StoreBinding:
    """Storage-layer view of a knowledge binding. Decoupled from the
    runbook YAML schema (``iac/runbook/models.py:KnowledgeBinding``) so
    the storage layer doesn't import upward — `from_binding` accepts
    this struct regardless of whether the caller is a runbook executor,
    a CLI command synthesizing one from flags, or a test.

    Same shape as ``messaging/_writer.py:MessageBindingResolved`` — the
    pattern is intentional: each plugin family has a resolved binding
    type that its `from_binding` factory consumes."""

    store: str = "file"
    name: str = ""
    root: str = ""
    company: str = ""
    config: Mapping[str, str] = field(default_factory=dict)


class KnowledgeStore(ABC):
    """Strategy contract. Each backend ships with a unique `name` that
    the registry indexes. Returning an empty string from `get()` is the
    "not found" convention — content is markdown so empty is unambiguous."""

    name: ClassVar[str] = ""

    @classmethod
    @abstractmethod
    def from_binding(cls, binding: StoreBinding, *, default_root: Optional[Path] = None) -> "KnowledgeStore":
        """Construct a store from a resolved binding.

        Each backend reads what it needs from ``binding.config`` (a
        ``Mapping[str, str]`` of free-form keys) and from ``binding.company``
        (for per-company env-var resolution). ``default_root`` is the CLI-level
        default file root — the file backend honours ``binding.root`` first
        and falls back to this; other backends ignore it."""

    @abstractmethod
    def put(self, blob_name: str, content: str, category: str = "") -> KnowledgeRef:
        """Create or update a blob. Returns the post-write reference."""

    @abstractmethod
    def get(self, blob_name: str) -> str:
        """Return the markdown content, or `""` when the blob is missing."""

    def get_many(self, names: Iterable[str]) -> Dict[str, str]:
        """Bulk fetch — `{name: content}` for every name found. Names
        that miss are omitted from the result (callers use `.get(name, "")`).

        Default implementation just calls `get()` per-name. Backends that
        can do this in one round-trip (Postgres uses `WHERE blob_name = ANY(%s)`)
        should override — that's the point of having this on the ABC, it lets
        callers (KnowledgeSplicer, dashboard collectors, plan context) be
        backend-agnostic while still avoiding the N+1 connection pattern."""
        out: Dict[str, str] = {}
        for name in names:
            content = self.get(name)
            if content:
                out[name] = content
        return out

    @abstractmethod
    def list(self, prefix: str = "") -> List[KnowledgeRef]:
        """Enumerate stored blobs. `prefix` matches against the start of
        the name — useful for filtering by category (e.g. `lessons:`)."""

    @abstractmethod
    def delete(self, blob_name: str) -> bool:
        """Return True if a row was removed, False if no such name."""

    def fingerprint(self, blob_name: str) -> str:
        """Hex MD5 of the stored content, or `""` when the blob is missing.

        Default implementation reads the full content and hashes it. Backends
        that can compute the digest server-side (e.g. Postgres `md5(content)`)
        should override for efficiency. The skipping caller uses this to avoid
        rewriting unchanged blobs — comparing fingerprints is cheap, comparing
        full content is not."""
        import hashlib

        content = self.get(blob_name)
        if not content:
            return ""
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def put_if_changed(self, blob_name: str, content: str, category: str = "") -> "PutIfChangedResult":
        """Write `content` only when its md5 differs from the stored blob's.

        Returns a structured result so callers can branch on `wrote` vs
        `skipped` without parsing log lines. Backends that can do the
        compare-and-set server-side (postgres) should override this for
        a single round-trip; the default implementation reads the
        fingerprint and conditionally calls `put`, costing whatever the
        backend's overhead is per call.

        The skip path leaves `updated_at` and history rows untouched,
        which is the whole point — downstream readers can use the
        unchanged `updated_at` to short-circuit reprocessing."""
        import hashlib

        new_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        existing = self.fingerprint(blob_name)
        if existing and existing == new_hash:
            return PutIfChangedResult(wrote=False, byte_count=len(content), new_hash=new_hash, prev_hash=existing)
        ref = self.put(blob_name, content, category=category)
        return PutIfChangedResult(wrote=True, byte_count=ref.byte_count, new_hash=new_hash, prev_hash=existing, ref=ref)


@dataclass
class PutIfChangedResult:
    """Outcome of `KnowledgeStore.put_if_changed`."""

    wrote: bool
    byte_count: int
    new_hash: str
    prev_hash: str = ""
    ref: "KnowledgeRef" = None  # type: ignore[assignment]
