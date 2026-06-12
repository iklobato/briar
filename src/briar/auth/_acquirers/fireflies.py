"""Fireflies.ai API key acquirer.

Walks the user to Fireflies settings → Developer Settings, prompts for
the paste. Stores ``FIREFLIES_<COMPANY>_API_KEY`` — the same env var the
``meeting-digest`` / ``meeting-context`` extractors read."""

from __future__ import annotations

from typing import List

from briar.auth._acquirer import CredentialAcquirer, Credentials
from briar.auth._prompt import PromptIO
from briar.env_vars import CredEnv


_API_KEY_URL = "https://app.fireflies.ai/settings"


class FirefliesApiKeyAcquirer(CredentialAcquirer):
    kind = "fireflies"
    display_name = "Fireflies.ai API key (paste)"

    def acquire(self, *, company: str, prompt: PromptIO) -> Credentials:
        if not company:
            raise ValueError("fireflies: --company is required")

        prompt.info("==> Fireflies.ai API key")
        prompt.info(f"    1. Open {_API_KEY_URL}")
        prompt.info("    2. Go to the 'Developer Settings' tab")
        prompt.info("    3. Copy the API Key")
        prompt.open_url(_API_KEY_URL)

        key = prompt.prompt("    paste API key: ", secret=True).strip()
        if not key:
            raise ValueError("fireflies: empty key")

        return Credentials(
            provider_kind=self.kind,
            entries={CredEnv.FIREFLIES_API_KEY.for_company(company): key},
        )

    @classmethod
    def writes(cls, *, company: str) -> List[str]:
        if not company:
            return []
        return [CredEnv.FIREFLIES_API_KEY.for_company(company)]
