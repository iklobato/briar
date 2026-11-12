"""`briar auth`: the logins briar uses to read the sources (spec 005).

- ``briar.auth.config``: the `[auth]` block of `.briar.toml` and the runbook
  `auth:` block, one table of choices per source.
- ``briar.auth.logins``: one login per source per company (`CompanyLogins`):
  the secret names, what `briar auth login` asks for, and the 1.x names guard.
- ``CredentialAcquirer`` (``_acquirer.py``): the 1.x interactive flows, kept for
  the AWS logins until spec 005 PR 2.
- ``briar.credentials``: `EnvFileStore`, the owner-only env file every secret
  goes to (L3), and the startup bootstrap that loads it."""

from __future__ import annotations

from briar.auth._acquirer import CredentialAcquirer, CredentialExpired, Credentials
from briar.auth._acquirers import ACQUIRERS, AcquirerRegistry, make_acquirer
from briar.auth._prompt import MockPromptIO, PromptIO, TerminalPromptIO


__all__ = [
    "ACQUIRERS",
    "AcquirerRegistry",
    "CredentialAcquirer",
    "CredentialExpired",
    "Credentials",
    "MockPromptIO",
    "PromptIO",
    "TerminalPromptIO",
    "make_acquirer",
]
