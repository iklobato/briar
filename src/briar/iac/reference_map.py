"""Cross-reference resolver.

Stores `(kind, key) → uuid` mappings populated incrementally as
reconcilers run. Strict mode (apply) raises on unresolved references;
lenient mode (plan) yields a placeholder so a dry-run can describe a
config whose dependencies aren't on the server yet."""

from __future__ import annotations

from typing import Dict

from briar.errors import ConfigError


class ReferenceMap:
    def __init__(self, *, lenient: bool = False) -> None:
        self._kinds: Dict[str, Dict[str, str]] = {}
        self._lenient = lenient

    @property
    def lenient(self) -> bool:
        return self._lenient

    def remember(self, kind: str, key: str, uuid: str) -> None:
        self._kinds.setdefault(kind, {})[key] = uuid

    def lookup(self, kind: str, key: str) -> str:
        uuid = self._kinds.get(kind, {}).get(key)
        if uuid:
            return uuid
        if self._lenient:
            return f"(unresolved:{kind}.{key})"
        raise ConfigError(
            f"reference to {kind}.{key} could not be resolved — "
            f"is it declared in the config or already present in the "
            f"workspace?"
        )
