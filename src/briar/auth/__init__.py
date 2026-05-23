"""`briar auth` — interactive credential acquisition + management.

Three abstractions:

- ``CredentialAcquirer`` (this package's ``_acquirer.py``) — the
  *write* side: one provider × auth-style per implementation. Walks
  the user through whatever interactive flow the vendor requires
  (OAuth device flow, paste a token, browser-extract a cookie, …)
  and returns a ``Credentials`` bundle.

- ``CredentialStore`` (``briar.credentials._store``) — the
  *persistence* side: file, AWS Secrets Manager, SSM, Vault. The
  ``briar auth login`` command pairs any acquirer with any store.

- ``CredentialBootstrap`` (``briar.credentials._bootstraps``) — the
  *bulk-hydrate* side: pull a whole environment from a remote vault
  at process startup (Infisical today). Orthogonal to acquisition;
  meant for already-provisioned secrets.

Each axis is independent: acquire via GitHub OAuth → persist to
Vault; or acquire via paste-a-token → persist to env-file; or skip
acquisition entirely and let CredentialBootstrap hydrate from
Infisical at startup."""

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
