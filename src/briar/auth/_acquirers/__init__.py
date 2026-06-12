"""Concrete ``CredentialAcquirer`` adapters + registry.

Adding a new acquirer = one module + one entry in ``ACQUIRERS``.
Same Strategy + Registry pattern as ``_trackers/``, ``_providers/``,
``_clouds/``, ``_writers/``, ``_jira_auth.py``."""

from __future__ import annotations

from typing import Dict, Tuple, Type

from briar._registry import build_registry
from briar.auth._acquirer import CredentialAcquirer
from briar.auth._acquirers.aws_sso import AwsSsoAcquirer
from briar.auth._acquirers.aws_static import AwsStaticAcquirer
from briar.auth._acquirers.bitbucket import BitbucketAppPasswordAcquirer
from briar.auth._acquirers.fireflies import FirefliesApiKeyAcquirer
from briar.auth._acquirers.github_device import GithubDeviceAcquirer
from briar.auth._acquirers.github_pat import GithubPatAcquirer
from briar.auth._acquirers.infisical import InfisicalAcquirer
from briar.auth._acquirers.jira_session import JiraSessionAcquirer
from briar.auth._acquirers.jira_token import JiraTokenAcquirer
from briar.auth._acquirers.linear import LinearApiKeyAcquirer
from briar.errors import CliError


ACQUIRERS: Dict[str, Type[CredentialAcquirer]] = build_registry(
    (
        GithubDeviceAcquirer,
        GithubPatAcquirer,
        BitbucketAppPasswordAcquirer,
        AwsStaticAcquirer,
        AwsSsoAcquirer,
        JiraTokenAcquirer,
        JiraSessionAcquirer,
        LinearApiKeyAcquirer,
        FirefliesApiKeyAcquirer,
        InfisicalAcquirer,
    ),
    kind="credential acquirer",
    name_attr="kind",
)


class AcquirerRegistry:
    """Factory + introspection — mirrors ``TrackerRegistry`` /
    ``_JIRA_AUTHS`` registry shape."""

    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(ACQUIRERS.keys())

    @classmethod
    def make(cls, kind: str) -> CredentialAcquirer:
        cls_obj = ACQUIRERS.get(kind)
        if cls_obj is None:
            known = ", ".join(sorted(ACQUIRERS.keys()))
            raise CliError(f"unknown credential acquirer {kind!r}; known: {known}")
        return cls_obj()


make_acquirer = AcquirerRegistry.make


__all__ = ["ACQUIRERS", "AcquirerRegistry", "make_acquirer"]
