"""Linear personal API key acquirer.

Walks the user to Linear settings → API → personal keys, prompts
for the paste. Stores ``LINEAR_<COMPANY>_TOKEN``."""

from __future__ import annotations

from typing import List

from briar.auth._acquirer import CredentialAcquirer, Credentials
from briar.auth._prompt import PromptIO
from briar.env_vars import CredEnv


_API_KEY_URL = "https://linear.app/settings/api"


class LinearApiKeyAcquirer(CredentialAcquirer):
    kind = "linear-api-key"
    display_name = "Linear personal API key (paste)"

    def acquire(self, *, company: str, prompt: PromptIO) -> Credentials:
        if not company:
            raise ValueError("linear-api-key: --company is required")

        prompt.info("==> Linear personal API key")
        prompt.info(f"    1. Open {_API_KEY_URL}")
        prompt.info("    2. Click 'New API key', name it `briar-<company>`")
        prompt.info("    3. Copy the key (you only see it once)")
        prompt.info("    Linear personal keys carry the user's full permissions.")
        prompt.open_url(_API_KEY_URL)

        key = prompt.prompt("    paste API key: ", secret=True).strip()
        if not key:
            raise ValueError("linear-api-key: empty key")

        return Credentials(
            provider_kind=self.kind,
            entries={CredEnv.LINEAR_TOKEN.for_company(company): key},
        )

    @classmethod
    def writes(cls, *, company: str) -> List[str]:
        if not company:
            return []
        return [CredEnv.LINEAR_TOKEN.for_company(company)]
