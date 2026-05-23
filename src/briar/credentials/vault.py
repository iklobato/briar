"""HashiCorp Vault `CredentialStore`.

Lazy-imports ``hvac``; opt-in via ``pip install briar-cli[vault]``.
KV v2 secrets at the ``briar/`` mount path; each credential is stored
as ``{value: "..."}``. Auth via ``VAULT_ADDR`` + ``VAULT_TOKEN`` env
vars (the basic flow — production deployments typically use AppRole
auth, which can be wired by swapping the constructor)."""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Dict, List, Optional

from briar.credentials._store import CredentialStore


log = logging.getLogger(__name__)


def _import_hvac() -> Optional[Any]:
    try:
        return importlib.import_module("hvac")
    except ImportError:
        return None


class VaultStore(CredentialStore):
    kind = "vault"
    MOUNT_POINT = "secret"  # default KV v2 mount
    PATH_PREFIX = "briar/"

    def __init__(self) -> None:
        self._addr = os.environ.get("VAULT_ADDR", "")
        self._token = os.environ.get("VAULT_TOKEN", "")
        self._client = None
        self._cache: Dict[str, str] = {}

    def _build_client(self):
        if self._client is not None:
            return self._client
        hvac = _import_hvac()
        if hvac is None:
            raise RuntimeError("hvac not installed — run `pip install briar-cli[vault]`")
        if not self._addr or not self._token:
            raise RuntimeError("VAULT_ADDR + VAULT_TOKEN env vars required for VaultStore")
        self._client = hvac.Client(url=self._addr, token=self._token)
        return self._client

    def read(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]
        # Silent miss when not configured — matches EnvFileStore semantics.
        # Raises only when configured AND the SDK is missing (i.e. operator
        # opted in but didn't install).
        if not (self._addr and self._token):
            self._cache[name] = ""
            return ""
        client = self._build_client()
        try:
            resp = client.secrets.kv.v2.read_secret_version(
                path=f"{self.PATH_PREFIX}{name}",
                mount_point=self.MOUNT_POINT,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("vault read miss name=%s err=%s", name, exc)
            self._cache[name] = ""
            return ""
        data = ((resp or {}).get("data") or {}).get("data") or {}
        value = str(data.get("value") or data.get(name) or "")
        self._cache[name] = value
        return value

    def write(self, name: str, value: str) -> None:
        """KV v2 create-or-update. Stores as ``{value: ...}`` so
        ``read`` can find it regardless of the operator's preferred
        key convention."""
        client = self._build_client()
        client.secrets.kv.v2.create_or_update_secret(
            path=f"{self.PATH_PREFIX}{name}",
            secret={"value": value},
            mount_point=self.MOUNT_POINT,
        )
        self._cache[name] = value

    def delete(self, name: str) -> bool:
        client = self._build_client()
        try:
            client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=f"{self.PATH_PREFIX}{name}",
                mount_point=self.MOUNT_POINT,
            )
        except Exception as exc:  # noqa: BLE001
            if "404" in str(exc):
                return False
            raise
        self._cache.pop(name, None)
        return True

    def list(self) -> List[str]:
        client = self._build_client()
        try:
            resp = client.secrets.kv.v2.list_secrets(path=self.PATH_PREFIX, mount_point=self.MOUNT_POINT)
        except Exception as exc:  # noqa: BLE001
            log.debug("vault list err=%s", exc)
            return []
        keys = ((resp or {}).get("data") or {}).get("keys") or []
        return sorted(str(k) for k in keys)
