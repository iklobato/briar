"""Credential-bootstrap registry — Strategy + Factory.

Each bootstrap fetches every secret from a remote vault at process
startup and writes them to `os.environ`. Distinct lifecycle from
`CredentialStore` (which is on-demand reads); see `_bootstrap.py`
for the contract.

Adding a new vault (Doppler, 1Password CLI, AWS Parameter Store
bulk-fetch, …) = one module here + one entry in the tuple. The
``auto_bootstrap()`` helper picks the first available one — typical
use is one bootstrap configured per host, not several."""

from __future__ import annotations

import logging
from typing import Dict, Tuple, Type

from briar._registry import build_registry
from briar.credentials._bootstrap import CredentialBootstrap, HydrateResult
from briar.credentials._bootstraps.infisical import InfisicalBootstrap
from briar.errors import CliError


log = logging.getLogger(__name__)


BOOTSTRAPS: Dict[str, Type[CredentialBootstrap]] = build_registry(
    (InfisicalBootstrap,),
    kind="credential bootstrap",
    name_attr="kind",
)


class CredentialBootstrapRegistry:
    """Factory + introspection. Static."""

    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(BOOTSTRAPS.keys())

    @classmethod
    def make(cls, kind: str) -> CredentialBootstrap:
        bs_cls = BOOTSTRAPS.get(kind)
        if bs_cls is None:
            known = ", ".join(sorted(BOOTSTRAPS.keys()))
            raise CliError(f"unknown credential bootstrap {kind!r}; known: {known}")
        return bs_cls()


make_bootstrap = CredentialBootstrapRegistry.make


def auto_bootstrap(*, dry_run: bool = False) -> HydrateResult:
    """Iterate every registered bootstrap, run the first one whose
    `is_available()` returns True. Called once from `briar.cli.main`
    before any command logic runs.

    Returns a `HydrateResult` even when no backend ran — callers
    branch on `.ok` and `.count`. Logged at INFO level so a fresh
    install with no bootstrap configured is self-explanatory in the
    journalctl tail."""
    for bs_cls in BOOTSTRAPS.values():
        bs = bs_cls()
        if not bs.is_available():
            log.debug("credential-bootstrap: %s not configured — skip", bs.kind)
            continue
        log.info("credential-bootstrap: running %s%s", bs.kind, " (dry-run)" if dry_run else "")
        return bs.hydrate(dry_run=dry_run)
    log.debug("credential-bootstrap: no backend configured — using os.environ as-is")
    return HydrateResult(backend="(none)")


__all__ = [
    "BOOTSTRAPS",
    "CredentialBootstrap",
    "CredentialBootstrapRegistry",
    "HydrateResult",
    "auto_bootstrap",
    "make_bootstrap",
]
