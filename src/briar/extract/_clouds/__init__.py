"""Cloud provider registry."""

from __future__ import annotations

from typing import Dict, Tuple, Type

from briar._registry import build_registry
from briar.errors import CliError
from briar.extract._cloud import CloudProvider
from briar.extract._clouds.aws import AwsCloudProvider
from briar.extract._clouds.azure import AzureCloudProvider
from briar.extract._clouds.gcp import GcpCloudProvider


CLOUDS: Dict[str, Type[CloudProvider]] = build_registry(
    (AwsCloudProvider, GcpCloudProvider, AzureCloudProvider),
    kind="cloud provider",
    name_attr="kind",
)


class CloudRegistry:
    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(CLOUDS.keys())

    @classmethod
    def make(cls, kind: str, *, company: str = "", region: str = "", profile: str = "") -> CloudProvider:
        cloud_cls = CLOUDS.get(kind)
        if cloud_cls is None:
            known = ", ".join(sorted(CLOUDS.keys()))
            raise CliError(f"unknown cloud provider {kind!r}; known: {known}")
        return cloud_cls(company=company, region=region, profile=profile)


make_cloud = CloudRegistry.make


__all__ = ["CLOUDS", "CloudProvider", "CloudRegistry", "make_cloud"]
