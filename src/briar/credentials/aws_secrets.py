"""AWS Secrets Manager `CredentialStore` — stub.

Implement via boto3 ``secretsmanager`` client. One secret per company
or one composite secret per environment. Read costs ~$0.05/10k calls
so the recommendation is to load all creds at process start and cache
in-memory."""

from __future__ import annotations

from typing import List

from briar.credentials._store import CredentialStore


class AwsSecretsManagerStore(CredentialStore):
    kind = "aws-secretsmanager"

    def read(self, name: str) -> str:
        raise NotImplementedError(
            "AwsSecretsManagerStore.read — boto3.client('secretsmanager').get_secret_value("
            "SecretId=name)['SecretString']. Cache the value on first read; SecretsManager "
            "charges per API call. For composite secrets (multiple key/value pairs under one "
            "SecretId) parse the SecretString as JSON and key into it."
        )

    def list(self) -> List[str]:
        raise NotImplementedError(
            "AwsSecretsManagerStore.list — boto3.client('secretsmanager').list_secrets() "
            "and map each entry to its Name. Paginate."
        )
