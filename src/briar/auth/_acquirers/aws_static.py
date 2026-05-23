"""AWS static-access-key acquirer.

Prompts for AccessKeyId + SecretAccessKey + region. No session
token (use ``aws-sso`` or external aws-vault/granted for STS-backed
credentials with auto-rotation)."""

from __future__ import annotations

from typing import List

from briar.auth._acquirer import CredentialAcquirer, Credentials
from briar.auth._prompt import PromptIO
from briar.env_vars import CredEnv


class AwsStaticAcquirer(CredentialAcquirer):
    kind = "aws-static"
    display_name = "AWS static access key (paste)"

    def acquire(self, *, company: str, prompt: PromptIO) -> Credentials:
        if not company:
            raise ValueError("aws-static: --company is required")
        prompt.info("==> AWS static keys")
        prompt.info("    Paste an IAM user's AccessKeyId + SecretAccessKey.")
        prompt.info("    The key needs only the permissions briar's extractors call:")
        prompt.info("      ec2:Describe*  rds:Describe*  ecs:List*+Describe*")
        prompt.info("      lambda:List*+Get*  logs:Describe*  sqs:List*+Get*")
        prompt.info("      sts:GetCallerIdentity")

        kid = prompt.prompt("    AccessKeyId (AKIA…): ").strip()
        secret = prompt.prompt("    SecretAccessKey: ", secret=True).strip()
        region = prompt.prompt("    Default region [us-east-1]: ").strip() or "us-east-1"
        if not (kid and secret):
            raise ValueError("aws-static: key id and secret required")

        return Credentials(
            provider_kind=self.kind,
            entries={
                CredEnv.AWS_KEY_ID.for_company(company): kid,
                CredEnv.AWS_SECRET.for_company(company): secret,
                CredEnv.AWS_REGION.for_company(company): region,
            },
            metadata={"auth_mode": "static-iam-user"},
        )

    @classmethod
    def writes(cls, *, company: str) -> List[str]:
        if not company:
            return []
        return [
            CredEnv.AWS_KEY_ID.for_company(company),
            CredEnv.AWS_SECRET.for_company(company),
            CredEnv.AWS_REGION.for_company(company),
        ]
