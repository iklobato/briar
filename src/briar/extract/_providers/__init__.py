"""Repository provider registry — Strategy + Factory.

Each concrete provider lives in its own module under `_providers/`
and is wired into `PROVIDERS` here. Adding a new vendor (GitLab,
Forgejo, SourceHut, …) = one module + one entry — no extractor
edits, no executor edits.

The factory `make_provider(kind, company)` is what extractors call
at run time. `company` is the per-tenant slug used by `CredEnv` to
resolve workspace-scoped credentials (Bitbucket app passwords are
per-workspace; GitHub PATs are workspace-wide so the parameter is
inert there)."""

from __future__ import annotations

from typing import Dict, Tuple, Type

from briar._registry import build_registry
from briar.errors import CliError
from briar.extract._provider import RepositoryProvider
from briar.extract._providers.bitbucket import BitbucketProvider
from briar.extract._providers.github import GithubProvider


PROVIDERS: Dict[str, Type[RepositoryProvider]] = build_registry(
    (GithubProvider, BitbucketProvider),
    kind="repository provider",
    name_attr="kind",
)


class RepositoryProviderRegistry:
    """Factory + introspection. Static-only — there's no per-process
    state worth caching; provider construction is cheap (env-var reads
    + a typed client). Re-constructing per extractor call keeps the
    surface dependency-free."""

    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(PROVIDERS.keys())

    @classmethod
    def make(cls, kind: str, company: str = "") -> RepositoryProvider:
        provider_cls = PROVIDERS.get(kind)
        if provider_cls is None:
            known = ", ".join(sorted(PROVIDERS.keys()))
            raise CliError(f"unknown repository provider {kind!r}; known: {known}")
        return provider_cls(company=company)


# Module-level alias kept stable.
make_provider = RepositoryProviderRegistry.make


__all__ = [
    "PROVIDERS",
    "RepositoryProvider",
    "RepositoryProviderRegistry",
    "make_provider",
]
