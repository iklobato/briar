"""`EnvFileStore` — thin wrapper over process env vars (which is
where `/etc/briar/secrets.env` lands once systemd reads it via
``EnvironmentFile=``).

This is the only `CredentialStore` backend that needs to work today.
It exposes the existing env-var surface through the new abstraction
so `briar secrets doctor` and any future store-backed code can use
one API regardless of where credentials live."""

from __future__ import annotations

import os
from typing import List

from briar.credentials._store import CredentialStore


# Canonical credential name prefixes — used by `list()` to enumerate
# which env vars are "credentials" vs unrelated process state.
# Updating CredEnv? Update this list too.
_KNOWN_PREFIXES: tuple = (
    "AWS_",
    "GITHUB_",
    "BITBUCKET_",
    "JIRA_",
    "LINEAR_",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "BRIAR_DATABASE_URL",
    "TELEGRAM_",
    "SLACK_",
    "SMTP_",
    "EMAIL_",
    "PAGERDUTY_",
)


class EnvFileStore(CredentialStore):
    kind = "envfile"

    def read(self, name: str) -> str:
        return os.environ.get(name, "")

    def list(self) -> List[str]:
        return sorted(k for k in os.environ if any(k.startswith(p) for p in _KNOWN_PREFIXES))

    def expires_at(self, name: str) -> str:
        """AWS STS session tokens carry expiry inside the token itself,
        but parsing that requires the STS GetSessionToken API. For
        env-file creds we can't tell — return ``""`` and let the
        operator rotate based on the local SSO session timeout."""
        return ""
