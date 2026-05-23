"""Jira API-token acquirer.

Walks the user to the Atlassian token page, then prompts for URL +
email + paste of the generated token. Stores ``JIRA_<COMPANY>_URL``,
``JIRA_<COMPANY>_EMAIL``, ``JIRA_<COMPANY>_TOKEN`` AND sets
``JIRA_<COMPANY>_AUTH_KIND=token`` so ``JiraAuthRegistry.autodetect``
always picks this strategy."""

from __future__ import annotations

from typing import List

from briar.auth._acquirer import CredentialAcquirer, Credentials
from briar.auth._prompt import PromptIO
from briar.env_vars import CredEnv


_TOKENS_URL = "https://id.atlassian.com/manage-profile/security/api-tokens"


class JiraTokenAcquirer(CredentialAcquirer):
    kind = "jira-token"
    display_name = "Jira API token (paste)"

    def acquire(self, *, company: str, prompt: PromptIO) -> Credentials:
        if not company:
            raise ValueError("jira-token: --company is required")

        prompt.info("==> Jira API token")
        prompt.info(f"    1. Open {_TOKENS_URL}")
        prompt.info("    2. Click 'Create API token', name it `briar-<company>`")
        prompt.info("    3. Copy the token (you only see it once)")
        prompt.open_url(_TOKENS_URL)

        url = prompt.prompt("    Jira URL (https://<org>.atlassian.net): ").strip().rstrip("/")
        email = prompt.prompt("    Atlassian account email: ").strip()
        token = prompt.prompt("    paste API token: ", secret=True).strip()
        if not (url and email and token):
            raise ValueError("jira-token: URL + email + token all required")

        return Credentials(
            provider_kind=self.kind,
            entries={
                CredEnv.JIRA_URL.for_company(company): url,
                CredEnv.JIRA_EMAIL.for_company(company): email,
                CredEnv.JIRA_TOKEN.for_company(company): token,
                CredEnv.JIRA_AUTH_KIND.for_company(company): "token",
            },
            metadata={"auth_mode": "api-token"},
        )

    @classmethod
    def writes(cls, *, company: str) -> List[str]:
        if not company:
            return []
        return [
            CredEnv.JIRA_URL.for_company(company),
            CredEnv.JIRA_EMAIL.for_company(company),
            CredEnv.JIRA_TOKEN.for_company(company),
            CredEnv.JIRA_AUTH_KIND.for_company(company),
        ]
