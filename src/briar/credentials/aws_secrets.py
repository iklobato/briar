"""AWS Secrets Manager `CredentialStore`.

Reads ``briar/<name>`` secrets at first call and caches in-memory for
the process lifetime (SecretsManager charges per API call). Region
comes from ``AWS_REGION`` env var or the boto3 default; auth uses the
ambient credential chain (IAM role on EC2, SSO profile locally,
static keys via env).

Composite secrets (a single ``SecretId`` whose ``SecretString`` is a
JSON object) are also supported via the ``key`` argument convention:
``store.read("BITBUCKET_ACME_USERNAME")`` → looks up secret
``briar/BITBUCKET_ACME_USERNAME``; if the SecretString parses as
JSON, the ``value`` key (or ``BITBUCKET_ACME_USERNAME``) wins."""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from briar.credentials._store import CredentialStore

log = logging.getLogger(__name__)


class AwsSecretsManagerStore(CredentialStore):
    kind = "aws-secretsmanager"
    PREFIX = "briar/"

    def __init__(self) -> None:
        self._client = None
        # Cache holds Optional[str]: None = confirmed-missing, str = value.
        self._cache: Dict[str, Optional[str]] = {}

    def _make_client(self):
        if self._client is None:
            from briar.credentials._aws import boto_client

            self._client = boto_client("secretsmanager")
        return self._client

    def read(self, name: str) -> Optional[str]:
        if name in self._cache:
            return self._cache[name]
        client = self._make_client()
        try:
            resp = client.get_secret_value(SecretId=f"{self.PREFIX}{name}")
        except client.exceptions.ResourceNotFoundException:
            log.debug("aws-secretsmanager read miss name=%s", name)
            self._cache[name] = None
            return None
        # Auth, throttling, network — propagate so callers fail closed.
        # The previous `except Exception` swallowed these and looked
        # identical to "secret doesn't exist," which masked revoked-IAM
        # incidents.
        value = resp.get("SecretString") or ""
        # Composite-secret support: if SecretString parses as JSON,
        # look for `{value}` or the bare key. Otherwise treat as scalar.
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                value = str(parsed.get("value") or parsed.get(name) or value)
        except (ValueError, TypeError):
            pass
        self._cache[name] = value
        return value

    def write(self, name: str, value: str) -> None:
        """Upsert via PutSecretValue; falls through to CreateSecret on
        first-time write. Uses typed `ResourceNotFoundException` for
        the create-vs-update decision so a permission-denied (which
        may contain "not found" in the message) doesn't silently
        trigger a CreateSecret attempt."""
        client = self._make_client()
        secret_id = f"{self.PREFIX}{name}"
        try:
            client.put_secret_value(SecretId=secret_id, SecretString=value)
        except client.exceptions.ResourceNotFoundException:
            client.create_secret(Name=secret_id, SecretString=value)
        self._cache[name] = value

    def delete(self, name: str) -> bool:
        """Force-delete with no recovery window."""
        client = self._make_client()
        secret_id = f"{self.PREFIX}{name}"
        try:
            client.delete_secret(SecretId=secret_id, ForceDeleteWithoutRecovery=True)
        except client.exceptions.ResourceNotFoundException:
            return False
        self._cache.pop(name, None)
        return True

    def list(self) -> List[str]:
        client = self._make_client()
        out: List[str] = []
        paginator = client.get_paginator("list_secrets")
        for page in paginator.paginate():
            for entry in page.get("SecretList", []) or []:
                full = entry.get("Name") or ""
                if full.startswith(self.PREFIX):
                    out.append(full[len(self.PREFIX) :])
        return sorted(out)
