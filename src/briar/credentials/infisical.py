"""Infisical `CredentialStore` — symmetric counterpart to
``InfisicalBootstrap``.

The Bootstrap is the BULK-HYDRATE side (pull every secret from a
project at process startup → into ``os.environ``). This Store is the
READ/WRITE-PER-NAME side (read/write/delete one secret on demand —
used by ``briar auth login --store infisical`` and any future
on-demand fetch).

Both share the same machine-identity credentials:
  INFISICAL_CLIENT_ID + INFISICAL_CLIENT_SECRET + INFISICAL_PROJECT_ID
(+ optional INFISICAL_ENV, defaults to ``prod``;
   + optional INFISICAL_HOST, defaults to https://app.infisical.com)

These are the values an `InfisicalAcquirer` walks the operator
through capturing — "logging into Infisical" in the briar UX is
really "give briar the machine-identity credentials it needs to
talk to Infisical from now on".

Lazy-imports ``infisical_sdk`` so it's an opt-in extra:
``pip install briar-cli[infisical]``."""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Dict, List, Optional

from briar.credentials._store import CredentialStore


log = logging.getLogger(__name__)


def _import_infisical_sdk() -> Optional[Any]:
    try:
        return importlib.import_module("infisical_sdk")
    except ImportError:
        return None


class InfisicalStore(CredentialStore):
    kind = "infisical"
    DEFAULT_HOST = "https://app.infisical.com"
    DEFAULT_ENV = "prod"
    SECRET_PATH = "/"  # KV-style flat namespace under the project

    def __init__(self) -> None:
        self._client_id = os.environ.get("INFISICAL_CLIENT_ID", "")
        self._client_secret = os.environ.get("INFISICAL_CLIENT_SECRET", "")
        self._project_id = os.environ.get("INFISICAL_PROJECT_ID", "")
        self._env = os.environ.get("INFISICAL_ENV", self.DEFAULT_ENV)
        self._host = os.environ.get("INFISICAL_HOST", self.DEFAULT_HOST)
        self._client = None
        self._cache: Dict[str, str] = {}

    def _build_client(self):
        """Universal-auth bind. Same dance as InfisicalBootstrap so a
        process that has both store + bootstrap pointed at the same
        Infisical project doesn't double-authenticate."""
        if self._client is not None:
            return self._client
        sdk = _import_infisical_sdk()
        if sdk is None:
            raise RuntimeError("infisical_sdk not installed — run `pip install briar-cli[infisical]`")
        if not all((self._client_id, self._client_secret, self._project_id)):
            raise RuntimeError(
                "InfisicalStore: missing INFISICAL_CLIENT_ID + _CLIENT_SECRET + _PROJECT_ID "
                "— run `briar auth login --provider infisical` to set them"
            )
        self._client = sdk.InfisicalSDKClient(host=self._host)
        self._client.auth.universal_auth.login(
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        return self._client

    # ── read side ────────────────────────────────────────────────────

    def read(self, name: str) -> str:
        """Silent miss when Infisical isn't configured — matches
        ``EnvFileStore`` semantics so the doctor can audit without
        forcing every caller to install the extra."""
        if name in self._cache:
            return self._cache[name]
        if not all((self._client_id, self._client_secret, self._project_id)):
            self._cache[name] = ""
            return ""
        try:
            client = self._build_client()
            resp = client.secrets.get_secret_by_name(
                secret_name=name,
                project_id=self._project_id,
                environment_slug=self._env,
                secret_path=self.SECRET_PATH,
            )
        except Exception as exc:  # noqa: BLE001 — translate to "missing" per the store contract
            log.debug("infisical-store read miss name=%s err=%s", name, exc)
            self._cache[name] = ""
            return ""
        # Tolerate both attribute + dict shapes (SDK versions differ).
        value = getattr(resp, "secretValue", None)
        if value is None and isinstance(resp, dict):
            value = resp.get("secretValue") or (resp.get("secret") or {}).get("secretValue")
        if value is None:
            secret = getattr(resp, "secret", None)
            value = getattr(secret, "secretValue", None) if secret is not None else None
        value = str(value or "")
        self._cache[name] = value
        return value

    # ── write side ───────────────────────────────────────────────────

    def write(self, name: str, value: str) -> None:
        """Upsert. Try ``update`` first — falls back to ``create`` if
        the secret doesn't exist yet (the symmetric pattern used by
        ``AwsSecretsManagerStore.write``)."""
        client = self._build_client()
        try:
            client.secrets.update_secret_by_name(
                secret_name=name,
                project_id=self._project_id,
                environment_slug=self._env,
                secret_path=self.SECRET_PATH,
                secret_value=value,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "not found" in msg or "notfound" in msg or "404" in msg:
                client.secrets.create_secret_by_name(
                    secret_name=name,
                    project_id=self._project_id,
                    environment_slug=self._env,
                    secret_path=self.SECRET_PATH,
                    secret_value=value,
                )
            else:
                log.exception("infisical-store write name=%s", name)
                raise
        self._cache[name] = value

    def delete(self, name: str) -> bool:
        client = self._build_client()
        try:
            client.secrets.delete_secret_by_name(
                secret_name=name,
                project_id=self._project_id,
                environment_slug=self._env,
                secret_path=self.SECRET_PATH,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "not found" in msg or "notfound" in msg or "404" in msg:
                return False
            raise
        self._cache.pop(name, None)
        return True

    # ── enumeration ──────────────────────────────────────────────────

    def list(self) -> List[str]:
        """Empty list when not configured — symmetric to read()."""
        if not all((self._client_id, self._client_secret, self._project_id)):
            return []
        try:
            client = self._build_client()
            resp = client.secrets.list_secrets(
                project_id=self._project_id,
                environment_slug=self._env,
                secret_path=self.SECRET_PATH,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("infisical-store list failed err=%s", exc)
            return []
        names: List[str] = []
        for s in (getattr(resp, "secrets", None) or []) if not isinstance(resp, dict) else (resp.get("secrets") or []):
            n = getattr(s, "secretKey", None)
            if n is None and isinstance(s, dict):
                n = s.get("secretKey", "")
            if n:
                names.append(str(n))
        return sorted(names)
