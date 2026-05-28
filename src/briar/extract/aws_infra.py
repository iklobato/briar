"""Cloud infrastructure extractor.

Cloud-provider-agnostic now. Talks to a `CloudProvider`; selecting
AWS / GCP / Azure is a runbook YAML flag. The legacy name
``aws-infra`` is preserved for back-compat with existing runbooks —
when ``--cloud aws`` (the default) is selected, this extractor walks
the existing `aws_services/` gatherers via `AwsCloudProvider`.

The per-cloud "what services to gather" decision lives inside each
`CloudProvider.list_*` verb, not here. The outer orchestration is
identical across vendors:
1. ``caller_identity()`` — surface account ID + region in the title
2. ``list_compute()`` / ``list_databases()`` / ``list_queues()`` /
   ``list_log_groups()`` — render normalised subsections."""

from __future__ import annotations

import argparse
from typing import List

from briar.extract.aws_services import AWS_SERVICE_GATHERERS
from briar.extract.base import CloudBackedExtractor, ExtractedSection


class ExtractAwsInfra(CloudBackedExtractor):
    name = "aws-infra"
    heading = "AWS infrastructure"
    description = "cloud resources via CloudProvider (AWS / GCP / Azure)"
    requires_aws = True  # legacy flag — kept for back-compat

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--aws-extract-profile",
            help="Local AWS profile name (falls back to default boto3 chain)",
        )
        parser.add_argument(
            "--aws-extract-region",
            default="us-east-1",
            help="AWS region to inspect (default: us-east-1)",
        )
        parser.add_argument(
            "--aws-extract-service",
            action="append",
            default=[],
            choices=sorted(AWS_SERVICE_GATHERERS.keys()),
            help="Which AWS services to include (repeatable; default: all)",
        )

    def is_available(self, args: argparse.Namespace) -> bool:
        try:
            cloud = self._cloud(args)
        except Exception:  # noqa: BLE001
            return False
        return cloud.is_available()

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        """One path regardless of cloud kind. The per-vendor rendering
        decision lives on the `CloudProvider` subclass — AwsCloudProvider
        overrides `list_subsections` to use the native gatherer
        registry, every other cloud inherits the generic walker.

        Removed in this commit: the `if cloud_kind == "aws"` branch
        that used to live here. See ARCHITECTURE.md finding #4."""
        cloud = self._cloud(args)
        try:
            identity = cloud.caller_identity()
        except Exception as exc:  # noqa: BLE001
            return ExtractedSection(
                title=f"{cloud.kind.upper()} infrastructure (UNREACHABLE)",
                body=f"Could not resolve caller identity — {exc}",
            )

        # AWS-specific filter — only the AWS subclass accepts `services=`.
        # The base CloudProvider's signature is `list_subsections()` (no
        # kwargs); ISP-respect means we don't force GCP/Azure to accept
        # a kwarg they'd silently ignore.
        from briar.extract._clouds.aws import AwsCloudProvider

        if isinstance(cloud, AwsCloudProvider):
            services_filter = vars(args).get("aws_extract_service") or None
            subsections = cloud.list_subsections(services=services_filter)
        else:
            subsections = cloud.list_subsections()
        return ExtractedSection(
            title=f"{cloud.kind.upper()} infrastructure — account {identity.account_id}, region {identity.region}",
            body="Live resource inventory at extract time.",
            subsections=subsections,
        )
