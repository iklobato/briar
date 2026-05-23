"""Bitbucket Cloud app-password acquirer.

Walks the user through app-password generation at
bitbucket.org → personal settings → app passwords, then prompts
for workspace + username + paste of the generated password.

Stores ``BITBUCKET_<COMPANY>_USERNAME``, ``BITBUCKET_<COMPANY>_APP_PASSWORD``,
``BITBUCKET_<COMPANY>_WORKSPACE`` (per-company because app passwords
are tied to a user × workspace pair, unlike GitHub PATs)."""

from __future__ import annotations

from typing import List

from briar.auth._acquirer import CredentialAcquirer, Credentials
from briar.auth._prompt import PromptIO
from briar.env_vars import CredEnv


_SETTINGS_URL = "https://bitbucket.org/account/settings/app-passwords/"


class BitbucketAppPasswordAcquirer(CredentialAcquirer):
    kind = "bitbucket-app-password"
    display_name = "Bitbucket Cloud app password (paste)"

    def acquire(self, *, company: str, prompt: PromptIO) -> Credentials:
        if not company:
            raise ValueError("bitbucket-app-password: --company is required")
        prompt.info("==> Bitbucket Cloud — app password")
        prompt.info(f"    1. Open {_SETTINGS_URL}")
        prompt.info("    2. Click 'Create app password'")
        prompt.info("    3. Required scopes: Repositories: Read + Write, Pull requests: Read + Write")
        prompt.info("    4. Copy the generated password (you only see it once)")
        prompt.open_url(_SETTINGS_URL)

        workspace = prompt.prompt("    Bitbucket workspace slug: ").strip()
        username = prompt.prompt("    Bitbucket username (NOT email): ").strip()
        password = prompt.prompt("    paste app password: ", secret=True).strip()
        if not (workspace and username and password):
            raise ValueError("bitbucket-app-password: all three fields required")

        return Credentials(
            provider_kind=self.kind,
            entries={
                CredEnv.BITBUCKET_WORKSPACE.for_company(company): workspace,
                CredEnv.BITBUCKET_USERNAME.for_company(company): username,
                CredEnv.BITBUCKET_APP_PASSWORD.for_company(company): password,
            },
        )

    @classmethod
    def writes(cls, *, company: str) -> List[str]:
        if not company:
            return []
        return [
            CredEnv.BITBUCKET_WORKSPACE.for_company(company),
            CredEnv.BITBUCKET_USERNAME.for_company(company),
            CredEnv.BITBUCKET_APP_PASSWORD.for_company(company),
        ]
