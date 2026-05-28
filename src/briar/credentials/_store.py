"""`CredentialStore` — Strategy contract for credential resolution.

Today, every cred is read directly from process env vars (via
``CredEnv``). This abstraction lets you keep the same call sites
(``store.read(env_name)``) while swapping the backing source —
EnvFile (today), AWS Secrets Manager, SSM Parameter Store, Vault.

Four verbs:
- ``read(name)``: look up one var by canonical name. Returns the value,
  or ``None`` if the credential is unknown to this store. Returning
  ``None`` distinguishes "not set" from "set to empty string" — auth
  failures should raise, not return ``None``, so callers can fail closed.
- ``list()``: enumerate names of all credentials known to the store
  (used by ``briar secrets doctor`` to report set/missing).
- ``fingerprint(name)``: keyed BLAKE2b digest of the stored value (for
  rotation detection without exposing plaintext).
- ``expires_at(name)``: parse expiration for time-bound creds (AWS STS
  session tokens). Returns ISO-8601 string or ``""``."""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, List, Optional


# Stable per-install salt for the fingerprint keyed hash. Derived from
# $HOME so it's deterministic for one operator but different across
# machines, preventing precomputed-rainbow-table comparisons. Cached
# per-process; cheap enough to compute once.
_FINGERPRINT_SALT: Optional[bytes] = None


def _fingerprint_salt() -> bytes:
    global _FINGERPRINT_SALT
    if _FINGERPRINT_SALT is None:
        seed = os.environ.get("HOME") or str(Path.home())
        _FINGERPRINT_SALT = hashlib.sha256(b"briar-fingerprint-v1:" + seed.encode("utf-8")).digest()
    return _FINGERPRINT_SALT


class CredentialStore(ABC):
    kind: ClassVar[str] = ""

    @abstractmethod
    def read(self, name: str) -> Optional[str]:
        """Return the value for `name`, or ``None`` if missing.

        Returning ``None`` means "the store does not have this name"
        (or the store is not configured). It MUST NOT be returned to
        paper over auth failures, network errors, or permission denials
        — those propagate as exceptions so callers can fail closed."""

    @abstractmethod
    def write(self, name: str, value: str) -> None:
        """Persist a credential. Overwrites existing values of the
        same name. Used by ``briar auth login`` after a successful
        acquire. Failures raise — callers propagate so the operator
        sees the underlying SDK error."""

    @abstractmethod
    def delete(self, name: str) -> bool:
        """Remove a credential. Returns True if a value existed and was
        removed, False if the name was unknown. Used by
        ``briar auth logout``."""

    @abstractmethod
    def list(self) -> List[str]:
        """Enumerate every credential name this store knows."""

    def fingerprint(self, name: str) -> str:
        """Keyed BLAKE2b-128 digest of the stored value, hex-encoded.

        Keyed so the digest can't be brute-forced against common-secret
        rainbow tables (the key is a stable per-install salt — see
        ``_fingerprint_salt``). Empty string if the credential is
        missing. Override for stores that can compute the digest
        server-side."""
        value = self.read(name)
        if not value:
            return ""
        return hashlib.blake2b(value.encode("utf-8"), key=_fingerprint_salt(), digest_size=16).hexdigest()

    def expires_at(self, name: str) -> str:
        """Default: no expiration tracking. AWS STS session tokens
        encode expiry inside the credential itself — overridden by
        the EnvFile / AwsSecretsManager stores."""
        return ""
