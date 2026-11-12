"""The credential store: the owner-only env file, `EnvFileStore` (L3). 1.x also
had AWS Secrets Manager, SSM and Vault; a `BRIAR_DEFAULT_STORE` that still names
one is stopped by `briar.credentials._removed_stores`. There is no store
interface since spec 005 (G16)."""

from __future__ import annotations

from briar.credentials.envfile import EnvFileStore

__all__ = ["EnvFileStore"]
