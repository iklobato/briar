"""HashiCorp Vault `CredentialStore` — stub.

Implement via ``hvac`` (Vault's Python client). Auth via
``VAULT_TOKEN`` env var or ``approle`` flow. KV v2 secrets at the
``briar/`` path."""

from __future__ import annotations

from typing import List

from briar.credentials._store import CredentialStore


class VaultStore(CredentialStore):
    kind = "vault"

    def read(self, name: str) -> str:
        raise NotImplementedError(
            "VaultStore.read — hvac.Client(url=os.environ['VAULT_ADDR'], "
            "token=os.environ['VAULT_TOKEN']).secrets.kv.v2.read_secret_version("
            "path=f'briar/{name}')['data']['data']['value']. Wrap KeyError as ''."
        )

    def list(self) -> List[str]:
        raise NotImplementedError(
            "VaultStore.list — client.secrets.kv.v2.list_secrets(path='briar/')['data']['keys']."
        )
