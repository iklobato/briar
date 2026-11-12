"""`CredentialAcquirer`: the interactive login flows of 1.x. Since spec 005 PR 1
only the AWS ones are left (``aws-static``, ``aws-sso``); PR 2 replaces them with
the AWS profile login. The pasted logins live in `briar.auth.logins`.

The acquirer's job ends at "produce a Credentials bundle"; `briar auth login`
writes it to the env file (`briar.credentials.EnvFileStore`)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Dict, List, Optional

from briar.auth._prompt import PromptIO


class CredentialExpired(Exception):
    """Raised by ``CredentialAcquirer.refresh`` when the existing
    bundle cannot be renewed without a fresh interactive login.
    Distinct from generic errors so the CLI can prompt the operator
    to re-run ``briar auth login`` instead of bailing."""


@dataclass(frozen=True)
class Credentials:
    """Provider-agnostic credential bundle.

    Why a flat ``entries`` dict keyed by env-var name instead of a
    typed model per provider: the env file store
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
    to the env file."""

    kind: ClassVar[str] = ""
    # Human-friendly display name; falls back to `kind` if empty.
    display_name: ClassVar[str] = ""

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
            f"run `briar auth login {self.kind} --company {company}`"
        )

    @classmethod
    @abstractmethod
    def writes(cls, *, company: str) -> List[str]:
        """Env-var names this acquirer writes (`briar auth status` checks them)."""


__all__ = ["CredentialAcquirer", "CredentialExpired", "Credentials"]
