"""`CredentialAcquirer` — interactive write side of credential
management. Symmetric to ``CredentialStore`` (read side) and
``CredentialBootstrap`` (bulk-hydrate side).

Each provider × auth-style gets its own acquirer (``github-device``,
``github-pat``, ``aws-static``, ``aws-sso``, ``jira-token``,
``jira-session``, …). Strategy + Registry, same shape as every other
plugin family in the codebase.

The acquirer's job ends at "produce a typed Credentials bundle".
Persistence is the ``CredentialStore``'s job — the two abstractions
are deliberately decoupled so the operator can pair any acquirer
with any store (paste a GitHub token, persist it to Vault; SSO into
AWS, persist into env-file)."""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Dict, List, Optional

from briar.auth._prompt import PromptIO


class DestinationPolicy(enum.Enum):
    """Where the acquired credentials are persisted.

    ``EXTERNAL`` (default) — vendor credentials like a GitHub PAT or
    AWS STS bundle. These can go in any ``CredentialStore`` — operator
    picks via ``--store``.

    ``BOOTSTRAP_LOCAL`` — the credentials DESCRIBE HOW TO REACH a
    ``CredentialStore`` (e.g. Infisical machine-identity, Vault
    address + token). They cannot be stored INSIDE that store —
    chicken-and-egg. Always persisted to the local ``envfile`` store;
    ``--store`` is ignored with a warning if the operator passes it."""

    EXTERNAL = "external"
    BOOTSTRAP_LOCAL = "bootstrap"


class CredentialExpired(Exception):
    """Raised by ``CredentialAcquirer.refresh`` when the existing
    bundle cannot be renewed without a fresh interactive login.
    Distinct from generic errors so the CLI can prompt the operator
    to re-run ``briar auth login`` instead of bailing."""


@dataclass(frozen=True)
class Credentials:
    """Provider-agnostic credential bundle.

    Why a flat ``entries`` dict keyed by env-var name instead of a
    typed model per provider: every ``CredentialStore`` already
    speaks env-var names (``store.read("AWS_ACME_ACCESS_KEY_ID")``).
    A flat dict lets the store ingest the bundle uniformly without
    knowing each provider's shape — the same decoupling that lets
    you mix-and-match acquirer × store."""

    provider_kind: str
    entries: Dict[str, str]
    expires_at: Optional[datetime] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    @property
    def names(self) -> List[str]:
        return sorted(self.entries.keys())


class CredentialAcquirer(ABC):
    """One vendor's interactive login flow.

    Concrete subclasses encode (a) which provider, (b) which
    auth-style (device flow vs paste vs SSO), and (c) which env
    vars get written. They DO NOT persist — the caller (the
    ``briar auth`` command) writes the returned ``Credentials``
    through a chosen ``CredentialStore``."""

    kind: ClassVar[str] = ""
    # Human-friendly display name; falls back to `kind` if empty.
    display_name: ClassVar[str] = ""
    # Where the result lands. Most acquirers obtain vendor credentials
    # that can be persisted to any store (EXTERNAL). Store-bootstrap
    # acquirers (Infisical, Vault) obtain the credentials needed to
    # talk to THAT store, so they must persist locally.
    destination_policy: ClassVar[DestinationPolicy] = DestinationPolicy.EXTERNAL

    @abstractmethod
    def acquire(self, *, company: str, prompt: PromptIO) -> Credentials:
        """Walk the user through this provider's login flow.

        May open a browser, prompt for paste, poll a device-code
        endpoint, etc. Implementations must catch their own
        provider-specific errors and translate to a user-facing
        message — only ``CredentialExpired`` and ``CliError`` should
        propagate."""

    def refresh(self, *, company: str, existing: Credentials) -> Credentials:
        """Renew an existing bundle without re-prompting where
        possible (OAuth refresh tokens, STS re-vend, …).

        Default: raise ``CredentialExpired`` — most paste-based flows
        can't refresh without a fresh interactive login. Override in
        OAuth / SSO acquirers."""
        raise CredentialExpired(
            f"{self.kind}: cannot refresh non-OAuth credentials — "
            f"run `briar auth login --provider {self.kind} --company {company}`"
        )

    @classmethod
    @abstractmethod
    def writes(cls, *, company: str) -> List[str]:
        """Env-var names this acquirer writes. Symmetric to the
        ``required_env_vars`` declared by ``RepositoryProvider`` /
        ``TrackerProvider`` / etc. — the doctor cross-checks
        acquired-vs-required to surface drift."""


__all__ = ["CredentialAcquirer", "CredentialExpired", "Credentials", "DestinationPolicy"]
