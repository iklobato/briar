"""AWS SSO / IAM Identity Center OIDC device-code acquirer.

Implements the same flow as ``aws sso login`` but vends STS
credentials directly into briar's env-var convention.

Three-step OIDC dance per the SSO spec:
  1. RegisterClient   → client_id, client_secret (cache friendly)
  2. StartDeviceAuthorization → device_code, user_code, verification_uri
  3. CreateToken (polled) → access_token

Then exchange the SSO access_token for STS credentials via
``sso.get_role_credentials(account_id, role_name, access_token)``.

Stores AWS_<COMPANY>_ACCESS_KEY_ID, _SECRET_ACCESS_KEY,
_SESSION_TOKEN, _REGION + records expires_at so the CLI can warn
the operator about the upcoming rotation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from briar.auth._acquirer import CredentialAcquirer, Credentials, CredentialExpired
from briar.auth._prompt import PromptIO
from briar.env_vars import CredEnv


log = logging.getLogger(__name__)


class AwsSsoAcquirer(CredentialAcquirer):
    kind = "aws-sso"
    display_name = "AWS IAM Identity Center (SSO device-code flow)"

    def acquire(self, *, company: str, prompt: PromptIO) -> Credentials:
        if not company:
            raise ValueError("aws-sso: --company is required")

        try:
            import boto3
            import botocore.exceptions
        except ImportError:
            raise RuntimeError("aws-sso: boto3 not installed — run `pip install briar-cli` (base)")

        prompt.info("==> AWS IAM Identity Center — device flow")
        start_url = prompt.prompt("    SSO start URL (e.g. https://acme.awsapps.com/start): ").strip()
        sso_region = prompt.prompt("    SSO region [us-east-1]: ").strip() or "us-east-1"
        if not start_url:
            raise ValueError("aws-sso: SSO start URL required")

        oidc = boto3.client("sso-oidc", region_name=sso_region)
        sso = boto3.client("sso", region_name=sso_region)

        # Step 1: register a client (one-shot per session).
        reg = oidc.register_client(clientName="briar-cli", clientType="public")
        client_id = reg["clientId"]
        client_secret = reg["clientSecret"]

        # Step 2: kick off device auth.
        device = oidc.start_device_authorization(
            clientId=client_id,
            clientSecret=client_secret,
            startUrl=start_url,
        )
        verification_uri = device.get("verificationUriComplete") or device["verificationUri"]
        user_code = device["userCode"]
        device_code = device["deviceCode"]
        interval = int(device.get("interval", 5))
        expires_in = int(device.get("expiresIn", 600))

        prompt.info(f"    1. Open {verification_uri}")
        prompt.info(f"    2. Verify the code: {user_code}")
        prompt.info(f"    3. Approve the briar-cli device")
        prompt.info(f"    polling every {interval}s for up to {expires_in}s …")
        prompt.open_url(verification_uri)

        # Step 3: poll for the SSO access token.
        def _poll_once() -> Optional[Dict[str, Any]]:
            try:
                return oidc.create_token(
                    clientId=client_id,
                    clientSecret=client_secret,
                    grantType="urn:ietf:params:oauth:grant-type:device_code",
                    deviceCode=device_code,
                )
            except botocore.exceptions.ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("AuthorizationPendingException", "SlowDownException"):
                    return None
                raise
            except Exception as exc:  # noqa: BLE001
                log.debug("aws-sso poll error: %s", exc)
                return None

        try:
            token_resp = prompt.poll(every=interval, max_wait=expires_in, fn=_poll_once)
        except TimeoutError:
            raise RuntimeError("aws-sso: timed out waiting for authorisation")

        access_token = token_resp["accessToken"]  # type: ignore[index]

        # Step 4: enumerate accounts/roles the operator has access to.
        accounts = []
        for page in sso.get_paginator("list_accounts").paginate(accessToken=access_token):
            accounts.extend(page.get("accountList", []))
        if not accounts:
            raise RuntimeError("aws-sso: no accounts visible to this SSO identity")

        if len(accounts) == 1:
            account = accounts[0]
        else:
            prompt.info("    Multiple accounts available:")
            for i, a in enumerate(accounts, start=1):
                prompt.info(f"      [{i}] {a['accountId']}  {a.get('accountName', '')}")
            idx = int(prompt.prompt("    Select account number: ").strip())
            account = accounts[idx - 1]
        account_id = account["accountId"]

        roles = []
        for page in sso.get_paginator("list_account_roles").paginate(accessToken=access_token, accountId=account_id):
            roles.extend(page.get("roleList", []))
        if not roles:
            raise RuntimeError(f"aws-sso: no roles for account {account_id}")
        if len(roles) == 1:
            role = roles[0]
        else:
            prompt.info(f"    Roles available for account {account_id}:")
            for i, r in enumerate(roles, start=1):
                prompt.info(f"      [{i}] {r['roleName']}")
            idx = int(prompt.prompt("    Select role number: ").strip())
            role = roles[idx - 1]
        role_name = role["roleName"]

        # Step 5: exchange for STS credentials.
        creds_resp = sso.get_role_credentials(
            accessToken=access_token,
            accountId=account_id,
            roleName=role_name,
        )
        rc = creds_resp["roleCredentials"]

        # Expiry is milliseconds since epoch in the SSO response.
        expires_at_ms = rc.get("expiration")
        expires_at = datetime.fromtimestamp(int(expires_at_ms) / 1000, tz=timezone.utc) if expires_at_ms else None

        region = prompt.prompt(f"    Default region for {company} extractions [{sso_region}]: ").strip() or sso_region

        return Credentials(
            provider_kind=self.kind,
            entries={
                CredEnv.AWS_KEY_ID.for_company(company): rc["accessKeyId"],
                CredEnv.AWS_SECRET.for_company(company): rc["secretAccessKey"],
                CredEnv.AWS_SESSION.for_company(company): rc.get("sessionToken", ""),
                CredEnv.AWS_REGION.for_company(company): region,
            },
            expires_at=expires_at,
            metadata={
                "auth_mode": "sso-device",
                "account_id": account_id,
                "role_name": role_name,
                "start_url": start_url,
                "sso_region": sso_region,
            },
        )

    def refresh(self, *, company: str, existing: Credentials) -> Credentials:
        """SSO refresh requires the operator's still-valid SSO access
        token (cached in ``~/.aws/sso/cache/`` by `aws sso login`).
        We don't cache it ourselves yet — punt to a full re-acquire."""
        raise CredentialExpired(
            f"aws-sso: refresh not implemented yet — run "
            f"`briar auth login --provider aws-sso --company {company}` to re-acquire"
        )

    @classmethod
    def writes(cls, *, company: str) -> List[str]:
        if not company:
            return []
        return [
            CredEnv.AWS_KEY_ID.for_company(company),
            CredEnv.AWS_SECRET.for_company(company),
            CredEnv.AWS_SESSION.for_company(company),
            CredEnv.AWS_REGION.for_company(company),
        ]
