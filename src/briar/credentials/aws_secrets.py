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
from typing import Dict, List

from briar.credentials._store import CredentialStore


log = logging.getLogger(__name__)


class AwsSecretsManagerStore(CredentialStore):
    kind = "aws-secretsmanager"
    PREFIX = "briar/"

    def __init__(self) -> None:
        self._client = None
        self._cache: Dict[str, str] = {}

    def _make_client(self):
        if self._client is not None:
            return self._client
        import boto3

        self._client = boto3.client("secretsmanager")
        return self._client

    def read(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]
        client = self._make_client()
        try:
            resp = client.get_secret_value(SecretId=f"{self.PREFIX}{name}")
        except Exception as exc:  # noqa: BLE001
            log.debug("aws-secretsmanager read miss name=%s err=%s", name, exc)
            self._cache[name] = ""
            return ""
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
