"""GitHub PAT (Personal Access Token) acquirer.

Walks the user through manual token generation on github.com →
settings → developer settings → personal access tokens, then
prompts for the paste. Stores ``GITHUB_TOKEN`` (no per-company
suffix — GitHub PATs are workspace-wide in this codebase)."""

from __future__ import annotations

from typing import List

from briar.auth._acquirer import CredentialAcquirer, Credentials
from briar.auth._prompt import PromptIO


_TOKENS_URL = "https://github.com/settings/tokens"


class GithubPatAcquirer(CredentialAcquirer):
    kind = "github-pat"
    display_name = "GitHub Personal Access Token (paste)"

    def acquire(self, *, company: str, prompt: PromptIO) -> Credentials:
        prompt.info("==> GitHub PAT — manual token generation")
        prompt.info(f"    1. Open {_TOKENS_URL}")
        prompt.info("    2. Click 'Generate new token (classic)'")
        prompt.info("    3. Required scopes:")
        prompt.info("         repo               (read PRs, push commits)")
        prompt.info("         read:org           (org membership for filters)")
        prompt.info("    4. Set expiration to 90 days (or your org policy)")
        prompt.info("    5. Copy the token (ghp_… or github_pat_…)")
        prompt.open_url(_TOKENS_URL)
        token = prompt.prompt("    paste token: ", secret=True).strip()
        if not token:
            raise ValueError("github-pat: empty token")
        return Credentials(
            provider_kind=self.kind,
            entries={"GITHUB_TOKEN": token},
            metadata={"label": f"briar-{company or 'default'}"},
        )

    @classmethod
    def writes(cls, *, company: str) -> List[str]:
        return ["GITHUB_TOKEN"]
