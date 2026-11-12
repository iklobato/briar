"""Stop a run whose `BRIAR_DEFAULT_STORE` names a store 2.0 removed.

1.x loaded every key from the store named there (SSM, Secrets Manager or
Vault) at startup. Without this check a 2.0 run would go on without those
keys and fail later with a less clear "missing credential" error."""

from __future__ import annotations

from typing import FrozenSet, Mapping

from briar.errors import CliError


class RemovedCredentialStoreGuard:
    """Raises one actionable `CliError` when the default store is a removed one."""

    ENV_VAR = "BRIAR_DEFAULT_STORE"
    REMOVED_KINDS: FrozenSet[str] = frozenset({"ssm", "aws-secretsmanager", "vault"})

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    def check(self) -> None:
        kind = (self._environ.get(self.ENV_VAR) or "").strip()
        if kind in self.REMOVED_KINDS:
            raise CliError(f"credential store {kind!r} was removed in briar 2.0; " f"unset {self.ENV_VAR} or move the keys to the env file (briar auth login)")
