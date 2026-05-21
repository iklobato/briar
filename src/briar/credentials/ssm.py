"""AWS SSM Parameter Store `CredentialStore` — stub.

SSM is cheaper than Secrets Manager (~$0.05/10k calls for standard
parameters, free for the first ~10k/month). Use ``SecureString``
parameters with KMS encryption."""

from __future__ import annotations

from typing import List

from briar.credentials._store import CredentialStore


class SsmParameterStore(CredentialStore):
    kind = "ssm"

    def read(self, name: str) -> str:
        raise NotImplementedError(
            "SsmParameterStore.read — boto3.client('ssm').get_parameter(Name='/briar/'+name, "
            "WithDecryption=True)['Parameter']['Value']. Convention: prefix parameters with "
            "/briar/<env>/ so IAM policies can grant access by path."
        )

    def list(self) -> List[str]:
        raise NotImplementedError(
            "SsmParameterStore.list — boto3.client('ssm').get_parameters_by_path("
            "Path='/briar/', Recursive=True). Paginate via NextToken."
        )
