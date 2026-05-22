"""Defensive `build_registry` helper.

Every plugin family in the codebase builds its `_REGISTRY` dict with
the same pattern: ``{x.name: x for x in (Item1(), Item2(), ...)}``.
That comprehension silently drops a duplicate-name collision (Python
dict-literal semantics: later assignment wins).

If two adapters ever claim the same `name` — say, two refactors both
typing ``kind = "github"`` — the registry would silently expose only
one. Hours of debugging.

This module's `build_registry()` does the exact same thing but raises
on a dup. Every registry in the codebase should use it."""

from __future__ import annotations

from typing import Any, Iterable, TypeVar


T = TypeVar("T")


def build_registry(items: Iterable[T], *, kind: str, name_attr: str = "name") -> dict:
    """Build the `{item.<name_attr>: item}` map, raising on a
    duplicate. `kind` is the human-readable family name shown in the
    error message ("repository provider", "tracker", "agent op", etc.)."""
    out: dict = {}
    for item in items:
        key = getattr(item, name_attr, None)
        if not key:
            raise RuntimeError(f"build_registry({kind}): item {item!r} has empty {name_attr!r}")
        if key in out:
            existing = out[key]
            raise RuntimeError(f"build_registry({kind}): duplicate {name_attr}={key!r} (already registered: {existing!r}, second: {item!r})")
        out[key] = item
    return out
