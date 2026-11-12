"""`briar auth` — interactive credential acquisition + management.

Three abstractions:

- ``CredentialAcquirer`` (this package's ``_acquirer.py``) — the
  *write* side: one provider × auth-style per implementation. Walks
  the user through whatever interactive flow the vendor requires
  (OAuth device flow, paste a token, browser-extract a cookie, …)
  and returns a ``Credentials`` bundle.

- ``CredentialStore`` (``briar.credentials._store``) — the
  *persistence* side. 2.0 has one store, the owner-only env file
  (1.x also had AWS Secrets Manager, SSM and Vault).

- ``CredentialBootstrap`` (``briar.credentials._bootstraps``) — the
  *bulk-hydrate* side: load the env file into the environment at
  process startup. Orthogonal to acquisition."""

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
