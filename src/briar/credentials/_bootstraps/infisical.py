"""Infisical `CredentialBootstrap`.

Lazy-imports `infisicalsdk` so it's an opt-in extra:
``pip install briar-cli[infisical]``.

Auth: universal-auth machine identity. Three env vars
(`INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID`)
plus optional `INFISICAL_ENV` (defaults to ``prod``) and
`INFISICAL_HOST` (defaults to ``https://app.infisical.com``).

`hydrate()` calls `client.secrets.list_secrets()` for the configured
project + environment, then `os.environ.setdefault(k, v)` for each
secret. Already-set env vars (e.g. populated by systemd before briar
starts) take precedence — matches the operator-intent-wins
semantics in the design doc."""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any, List, Optional

from briar.credentials._bootstrap import CredentialBootstrap, HydrateResult


log = logging.getLogger(__name__)


def _import_infisical_sdk() -> Optional[Any]:
    try:
        return importlib.import_module("infisical_sdk")
    except ImportError:
        return None


class InfisicalBootstrap(CredentialBootstrap):
    kind = "infisical"
    DEFAULT_HOST = "https://app.infisical.com"
    DEFAULT_ENV = "prod"

    REQUIRED_VARS: tuple = (
        "INFISICAL_CLIENT_ID",
        "INFISICAL_CLIENT_SECRET",
        "INFISICAL_PROJECT_ID",
    )

    def __init__(self) -> None:
        self._client_id = os.environ.get("INFISICAL_CLIENT_ID", "")
        self._client_secret = os.environ.get("INFISICAL_CLIENT_SECRET", "")
        self._project_id = os.environ.get("INFISICAL_PROJECT_ID", "")
        self._env = os.environ.get("INFISICAL_ENV", self.DEFAULT_ENV)
        self._host = os.environ.get("INFISICAL_HOST", self.DEFAULT_HOST)
        self._client = None

    def is_available(self) -> bool:
        return all((self._client_id, self._client_secret, self._project_id))

    def hydrate(self, *, dry_run: bool = False) -> HydrateResult:
        if not self.is_available():
            return HydrateResult(backend=self.kind, error="missing INFISICAL_{CLIENT_ID,CLIENT_SECRET,PROJECT_ID}")
        try:
            secrets = self._fetch_secrets()
        except Exception as exc:  # noqa: BLE001 — surface as result, don't crash startup
            log.exception("infisical fetch failed")
            return HydrateResult(backend=self.kind, error=f"fetch failed: {exc}")

        written: List[str] = []
        skipped: List[str] = []
        for key, value in secrets:
            if not key:
                continue
            if key in os.environ:
                skipped.append(key)
                continue
            if not dry_run:
                # setdefault — but we already checked `key not in os.environ`
                # above so this is effectively just `os.environ[key] = value`.
                # Kept as setdefault for the race-safe semantics under
                # concurrent imports.
                os.environ.setdefault(key, value)
            written.append(key)

        log.info(
            "infisical-bootstrap: backend=%s wrote=%d preserved=%d host=%s env=%s%s",
            self.kind,
            len(written),
            len(skipped),
            self._host,
            self._env,
            " (DRY RUN — nothing written)" if dry_run else "",
        )
        return HydrateResult(backend=self.kind, written=written, skipped=skipped)

    def _fetch_secrets(self) -> List[tuple]:
        sdk = _import_infisical_sdk()
        if sdk is None:
            raise RuntimeError("infisical_sdk not installed — run `pip install briar-cli[infisical]`")
        if self._client is None:
            self._client = sdk.InfisicalSDKClient(host=self._host)
            self._client.auth.universal_auth.login(
                client_id=self._client_id,
                client_secret=self._client_secret,
            )
        result = self._client.secrets.list_secrets(
            project_id=self._project_id,
            environment_slug=self._env,
            secret_path="/",
        )
        # Tolerate both attribute and dict shapes since SDK versions vary.
        return [(getattr(s, "secretKey", None) or s.get("secretKey", ""), getattr(s, "secretValue", None) or s.get("secretValue", "")) for s in (result.secrets or [])]

    @classmethod
    def required_env_vars(cls) -> List[str]:
        return list(cls.REQUIRED_VARS)
