"""`CredentialBootstrap` — bulk-write-at-startup credential hydration.

Distinct from `CredentialStore` (read-on-demand at the call site).
Where `CredentialStore.read(name)` is invoked by the doctor + by
adapters that need one specific value, `CredentialBootstrap.hydrate()`
runs ONCE at process startup, fetches every secret from a remote
store, and writes them to `os.environ` so the rest of briar (the
`CredEnv` enum, providers, writers, …) reads them through the
standard env-var path.

Two operating modes coexist:

  /etc/briar/secrets.env        →  systemd EnvironmentFile=…  →  os.environ
                                                                     ↑
                                                       briar reads here

  INFISICAL_CLIENT_ID + creds   →  InfisicalBootstrap.hydrate()  →  os.environ
                                                                     ↑
                                                       briar reads here

The local file path is the simpler default. Bootstrap mode is for
deployments where secrets live in a remote vault and the operator
only wants to bake the machine-identity credentials onto the host —
everything else is fetched at start.

Strategy + Registry behind `_bootstraps/`; constructed by
`make_bootstrap(kind)` or auto-selected by `auto_bootstrap()` (called
from `briar.cli.main` before any command logic runs)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Dict, List


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HydrateResult:
    """Outcome of one bootstrap. `written` lists the env-var names
    that were `os.environ.setdefault`-ed; `skipped` lists the names
    that were already set in `os.environ` and therefore preserved
    (caller intent wins over the remote vault)."""

    backend: str
    written: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def count(self) -> int:
        return len(self.written)


class CredentialBootstrap(ABC):
    """Strategy contract. Concrete adapters wrap one remote vault."""

    kind: ClassVar[str] = ""

    @abstractmethod
    def is_available(self) -> bool:
        """True iff the bootstrap has the machine-identity credentials
        it needs to authenticate against the remote vault. Without
        this signal, `auto_bootstrap()` skips the backend (so a host
        without Infisical creds is a no-op, not an error)."""

    @abstractmethod
    def hydrate(self, *, dry_run: bool = False) -> HydrateResult:
        """Fetch every secret + write to `os.environ`. Uses
        `setdefault` so an already-set env var (e.g. one populated by
        systemd's EnvironmentFile=) takes precedence over the remote
        value.

        `dry_run=True` performs the remote fetch but does NOT write —
        useful for `briar secrets bootstrap --dry-run` to see what
        WOULD be set without leaking values into the process env.
        Returns the same HydrateResult shape; `written` lists what
        the non-dry-run would have written."""

    @classmethod
    def required_env_vars(cls) -> List[str]:
        """The machine-identity env vars this bootstrap needs to
        authenticate (NOT the secrets it fetches — those are
        per-deployment). Used by `briar secrets doctor` to check
        whether the bootstrap itself is configured."""
        return []
