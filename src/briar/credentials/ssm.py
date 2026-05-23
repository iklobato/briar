"""AWS SSM Parameter Store `CredentialStore`.

SSM is significantly cheaper than Secrets Manager (free for the first
~10k standard parameter ops/month). Use ``SecureString`` parameters
with KMS encryption — this store always passes ``WithDecryption=True``.

Convention: parameter names are prefixed with ``/briar/`` so a single
IAM policy can grant access by path."""

from __future__ import annotations

import logging
from typing import Dict, List

from briar.credentials._store import CredentialStore


log = logging.getLogger(__name__)


class SsmParameterStore(CredentialStore):
    kind = "ssm"
    PREFIX = "/briar/"

    def __init__(self) -> None:
        self._client = None
        self._cache: Dict[str, str] = {}

    def _make_client(self):
        if self._client is not None:
            return self._client
        import boto3

        self._client = boto3.client("ssm")
        return self._client

    def read(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]
        client = self._make_client()
        try:
            resp = client.get_parameter(Name=f"{self.PREFIX}{name}", WithDecryption=True)
        except Exception as exc:  # noqa: BLE001
            log.debug("ssm read miss name=%s err=%s", name, exc)
            self._cache[name] = ""
            return ""
        value = str((resp.get("Parameter") or {}).get("Value") or "")
        self._cache[name] = value
        return value

    def write(self, name: str, value: str) -> None:
        """Upsert via PutParameter with Overwrite=True. SecureString
        type forces KMS encryption (default key under the account)."""
        client = self._make_client()
        client.put_parameter(
            Name=f"{self.PREFIX}{name}",
            Value=value,
            Type="SecureString",
            Overwrite=True,
        )
        self._cache[name] = value

    def delete(self, name: str) -> bool:
        client = self._make_client()
        try:
            client.delete_parameter(Name=f"{self.PREFIX}{name}")
        except Exception as exc:  # noqa: BLE001
            if "parameternotfound" in str(exc).lower():
                return False
            raise
        self._cache.pop(name, None)
        return True

    def list(self) -> List[str]:
        client = self._make_client()
        out: List[str] = []
        paginator = client.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=self.PREFIX, Recursive=True, WithDecryption=False):
            for entry in page.get("Parameters", []) or []:
                full = entry.get("Name") or ""
                if full.startswith(self.PREFIX):
                    out.append(full[len(self.PREFIX) :])
        return sorted(out)
