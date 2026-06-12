"""Credential store registry. All four backends (EnvFileStore,
AwsSecretsManagerStore, SsmParameterStore, VaultStore) are live; pick
one per company via `CredentialStoreRegistry.make(kind)`."""

from __future__ import annotations

from typing import Dict, Tuple, Type

from briar._registry import build_registry
from briar.credentials._store import CredentialStore
from briar.credentials.aws_secrets import AwsSecretsManagerStore
from briar.credentials.envfile import EnvFileStore
from briar.credentials.ssm import SsmParameterStore
from briar.credentials.vault import VaultStore
from briar.errors import CliError


STORES: Dict[str, Type[CredentialStore]] = build_registry(
    (EnvFileStore, AwsSecretsManagerStore, SsmParameterStore, VaultStore),
    kind="credential store",
    name_attr="kind",
)


class CredentialStoreRegistry:
    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(STORES.keys())

    @classmethod
    def make(cls, kind: str) -> CredentialStore:
        store_cls = STORES.get(kind)
        if store_cls is None:
            known = ", ".join(sorted(STORES.keys()))
            raise CliError(f"unknown credential store {kind!r}; known: {known}")
        return store_cls()


make_credential_store = CredentialStoreRegistry.make


__all__ = [
    "STORES",
    "CredentialStore",
    "CredentialStoreRegistry",
    "make_credential_store",
]
