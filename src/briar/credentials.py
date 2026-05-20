"""Credentials value-object + filesystem store.

`Credentials` is a frozen-ish dataclass holding API base, JWT pair,
workspace id, and email. `CredentialsStore` adapts it to disk —
load/save/clear at a profile path.

Style notes:
- No `getattr`; we iterate `__dataclass_fields__` directly.
- No `global`; the path is injected.
- No `elif`/`else`; early returns + dict-shaped construction.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

from briar.env_vars import CredEnv
from briar.settings import DEFAULT_API_BASE


@dataclass
class Credentials:
    """Per-profile persisted client state.

    Mutable on purpose — login flows overwrite `access`/`refresh` in
    place; the surrounding `CredentialsStore` flushes to disk."""

    api_base: str = DEFAULT_API_BASE
    access: str = ""
    refresh: str = ""
    workspace: str = ""
    email: str = ""

    @classmethod
    def from_disk(cls, path: Path) -> "Credentials":
        try:
            raw = path.read_text()
        except FileNotFoundError:
            return cls()
        data = json.loads(raw)
        defaults = cls()
        kwargs: Dict[str, Any] = {}
        for field_name in cls.__dataclass_fields__:
            value = data.get(field_name)
            kwargs[field_name] = (
                value if value else object.__getattribute__(defaults, field_name)
            )
        return cls(**kwargs)

    @classmethod
    def from_env(cls, profile: str) -> "Credentials | None":
        """Build creds from env vars only. Returns None when no
        Briar-family env var is set for this profile (so the caller
        can fall back to disk)."""
        access = CredEnv.BRIAR_ACCESS.read(profile)
        refresh = CredEnv.BRIAR_REFRESH.read(profile)
        if not (access or refresh):
            return None
        defaults = cls()
        return cls(
            api_base=CredEnv.BRIAR_API_BASE.read() or defaults.api_base,
            access=access or "",
            refresh=refresh or "",
            workspace=CredEnv.BRIAR_WORKSPACE_ID.read(profile) or "",
            email="",
        )

    def to_disk(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Best-effort. On platforms without POSIX permissions the
            # chmod silently no-ops; the file is still written.
            pass


class CredentialsStore:
    """Adapter that ties a `Credentials` value to its on-disk path.

    Provides `save()` so callers don't have to thread the path through
    every login / refresh / logout site, and `clear()` so logout has a
    single named operation rather than a series of attribute writes."""

    def __init__(self, path: Path, profile: str = "") -> None:
        self._path = path
        env_creds = Credentials.from_env(profile) if profile else None
        self._env_loaded = env_creds is not None
        self.creds = env_creds or Credentials.from_disk(path)

    @property
    def path(self) -> Path:
        return self._path

    def save(self) -> None:
        # When env vars are the source of truth (e.g. on the headless
        # scheduler droplet) we never write back to disk - the env owns
        # the value, persistence would silently override the next sync.
        if self._env_loaded:
            return
        self.creds.to_disk(self._path)

    def clear(self) -> None:
        self.creds.access = ""
        self.creds.refresh = ""
        self.creds.workspace = ""
        self.creds.email = ""
        self.save()
