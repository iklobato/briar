"""`CredentialStore` — Strategy contract for credential resolution.

Today, every cred is read directly from process env vars (via
``CredEnv``). This abstraction lets you keep the same call sites
(``store.read(env_name)``) while swapping the backing source —
EnvFile (today), AWS Secrets Manager, SSM Parameter Store, Vault.

Four verbs:
- ``read(name)``: look up one var by canonical name (e.g.
  ``AWS_ACME_ACCESS_KEY_ID``). Returns ``""`` if not found —
  callers check truthiness.
- ``list()``: enumerate names of all credentials known to the store
  (used by ``briar secrets doctor`` to report set/missing).
- ``fingerprint(name)``: md5 of the stored value (for rotation detection
  without exposing the value itself).
- ``expires_at(name)``: parse expiration for time-bound creds (AWS STS
  session tokens). Returns ISO-8601 string or ``""``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, List


class CredentialStore(ABC):
    kind: ClassVar[str] = ""

    @abstractmethod
    def read(self, name: str) -> str:
        """Return the value for `name`, or ``""`` if missing."""

    @abstractmethod
    def list(self) -> List[str]:
        """Enumerate every credential name this store knows."""

    def fingerprint(self, name: str) -> str:
        """Default: md5 of the value. Override for stores that can
        compute hashes server-side."""
        import hashlib

        value = self.read(name)
        if not value:
            return ""
        return hashlib.md5(value.encode("utf-8")).hexdigest()

    def expires_at(self, name: str) -> str:
        """Default: no expiration tracking. AWS STS session tokens
        encode expiry inside the credential itself — overridden by
        the EnvFile / AwsSecretsManager stores."""
        return ""
