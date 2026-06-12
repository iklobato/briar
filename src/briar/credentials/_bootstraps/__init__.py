"""Credential-bootstrap registry — Strategy + Factory.

Each bootstrap reads credentials from one source (local envfile,
remote vault) at process startup and writes them to `os.environ`.
Distinct lifecycle from `CredentialStore` (which is on-demand reads);
see `_bootstrap.py` for the contract.

Adding a new vault (Doppler, 1Password CLI, AWS Parameter Store
bulk-fetch, …) = one module here + one entry in the tuple. The
``auto_bootstrap()`` helper runs every available backend in
registry order — earlier backends "win" because later ones can
only set vars not yet present in ``os.environ``.

Registry order is the precedence order: envfile (laptop default
+ droplet via systemd) runs FIRST so locally-persisted creds beat
any later remote-vault values on conflict. Today envfile is the only
registered bootstrap."""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple, Type

from briar._registry import build_registry
from briar.credentials._bootstrap import CredentialBootstrap, HydrateResult
from briar.credentials._bootstraps.envfile import EnvFileBootstrap
from briar.errors import CliError


log = logging.getLogger(__name__)


# Order matters — see module docstring. EnvFileBootstrap first so
# locally-persisted creds always win.
BOOTSTRAPS: Dict[str, Type[CredentialBootstrap]] = build_registry(
    (EnvFileBootstrap,),
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


def auto_bootstrap(*, dry_run: bool = False) -> List[HydrateResult]:
    """Run every registered bootstrap whose `is_available()` returns
    True, in registry order. Called once from `briar.cli.main` before
    any command logic runs.

    Returns one `HydrateResult` per backend that ran. An empty list
    means no backend was configured — startup proceeds with
    ``os.environ`` as-is. Callers iterate the list and log each
    result independently; treating one failure as fatal would defeat
    the cascade (a later backend's failure should not erase a
    successful earlier hydrate).

    Cascade semantics: each bootstrap calls ``os.environ.setdefault``
    (or equivalent), so the first backend that supplies a given key
    wins. Operator intent (shell env > envfile > remote vault) is
    preserved by registry ordering."""
    results: List[HydrateResult] = []
    for bs_cls in BOOTSTRAPS.values():
        bs = bs_cls()
        if not bs.is_available():
            log.debug("credential-bootstrap: %s not configured — skip", bs.kind)
            continue
        log.info("credential-bootstrap: running %s%s", bs.kind, " (dry-run)" if dry_run else "")
        results.append(bs.hydrate(dry_run=dry_run))
    if not results:
        log.debug("credential-bootstrap: no backend configured — using os.environ as-is")
    return results


__all__ = [
    "BOOTSTRAPS",
    "CredentialBootstrap",
    "CredentialBootstrapRegistry",
    "HydrateResult",
    "auto_bootstrap",
    "make_bootstrap",
]
