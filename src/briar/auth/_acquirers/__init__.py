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
from briar.errors import CliError


ACQUIRERS: Dict[str, Type[CredentialAcquirer]] = build_registry(
    # Spec 005 PR 1 moved the pasted logins to `briar.auth.logins`; the AWS ones move in PR 2.
    (
        AwsStaticAcquirer,
        AwsSsoAcquirer,
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
