"""Default-store `CredentialBootstrap`.

`briar auth login` saves to ``$BRIAR_DEFAULT_STORE`` when that names a
registered store. When it names a remote one (``ssm``,
``aws-secretsmanager``, ``vault``), this bootstrap loads every key from
it into ``os.environ`` at startup, so the keys a login saved there reach
the other commands. Runs after the envfile bootstrap and uses
``setdefault``: the shell and the envfile both win over the remote store.

A store that cannot be read (no AWS access, Vault down) is reported as a
failed `HydrateResult`; startup goes on with what it has."""

from __future__ import annotations

import logging
import os
from typing import List

from briar.credentials import CredentialStoreRegistry, make_credential_store
from briar.credentials._bootstrap import CredentialBootstrap, HydrateResult
from briar.credentials._bootstraps.envfile import _DENY_ENV_VARS

log = logging.getLogger(__name__)

_LOCAL_STORE = "envfile"


def _remote_store_kind() -> str:
    kind = (os.environ.get("BRIAR_DEFAULT_STORE", "") or "").strip()
    if kind == _LOCAL_STORE or kind not in CredentialStoreRegistry.kinds():
        return ""
    return kind


class DefaultStoreBootstrap(CredentialBootstrap):
    kind = "default-store"

    def is_available(self) -> bool:
        return bool(_remote_store_kind())

    def hydrate(self, *, dry_run: bool = False) -> HydrateResult:
        store_kind = _remote_store_kind()
        try:
            store = make_credential_store(store_kind)
            names = store.list()
            values = {name: store.read(name) for name in names if name not in _DENY_ENV_VARS}
        except Exception as exc:  # noqa: BLE001 — a vault outage must not stop the CLI
            log.warning("default-store-bootstrap: %s read failed: %s", store_kind, exc)
            return HydrateResult(backend=self.kind, error=f"{store_kind} read failed: {exc}")

        written: List[str] = []
        skipped: List[str] = []
        for name, value in values.items():
            if value is None:
                continue
            if name in os.environ:
                skipped.append(name)
                continue
            if not dry_run:
                os.environ[name] = value
            written.append(name)
        return HydrateResult(backend=self.kind, written=written, skipped=skipped)

    @classmethod
    def required_env_vars(cls) -> List[str]:
        return ["BRIAR_DEFAULT_STORE"]
