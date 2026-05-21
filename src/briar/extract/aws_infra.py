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
        # AWS gets the legacy gatherer-by-gatherer path so the
        # markdown body shape stays identical for back-compat. Other
        # clouds use the CloudProvider abstraction directly — they
        # don't have legacy callers to preserve.
        cloud_kind = (vars(args).get("cloud") or "aws").lower()
        if cloud_kind == "aws":
            return self._extract_aws_legacy(args)
        return self._extract_via_cloud_provider(args)

    def _extract_aws_legacy(self, args: argparse.Namespace) -> ExtractedSection:
        """Preserves the original aws-infra markdown shape. Uses the
        per-service gatherers directly (each one renders its own
        section body) so the dashboard + downstream agents see the
        same output bytes as before this refactor."""
        import boto3

        from briar.env_vars import CredEnv

        ns = vars(args)
        profile = ns.get("aws_extract_profile") or ""
        region = args.aws_extract_region
        key_id = CredEnv.AWS_KEY_ID.read(profile) if profile else None
        secret = CredEnv.AWS_SECRET.read(profile) if profile else None
        if key_id and secret:
            session = boto3.Session(
                aws_access_key_id=key_id,
                aws_secret_access_key=secret,
                aws_session_token=CredEnv.AWS_SESSION.read(profile),
                region_name=region,
            )
        else:
            session = boto3.Session(profile_name=profile or None, region_name=region)

        try:
            acct = session.client("sts").get_caller_identity().get("Account", "?")
        except Exception as exc:  # noqa: BLE001
            return ExtractedSection(
                title="AWS infrastructure (UNREACHABLE)",
                body=f"Could not call sts:GetCallerIdentity — {exc}",
            )

        selected = args.aws_extract_service or list(AWS_SERVICE_GATHERERS.keys())
        subsections: List[ExtractedSection] = []
        for svc_name in selected:
            gatherer = AWS_SERVICE_GATHERERS.get(svc_name)
            if gatherer is None:
                continue
            try:
                section = gatherer.gather(session)
            except Exception as exc:  # noqa: BLE001
                section = ExtractedSection(title=svc_name.upper(), body=f"_skipped — {exc}_")
            if not section.is_empty:
                subsections.append(section)

        return ExtractedSection(
            title=(f"AWS infrastructure — account {acct}, region {args.aws_extract_region}"),
            body="Live resource inventory at extract time.",
            subsections=subsections,
        )

    def _extract_via_cloud_provider(self, args: argparse.Namespace) -> ExtractedSection:
        cloud = self._cloud(args)
        try:
            identity = cloud.caller_identity()
        except Exception as exc:  # noqa: BLE001
            return ExtractedSection(
                title=f"{cloud.kind.upper()} infrastructure (UNREACHABLE)",
                body=f"Could not resolve caller identity — {exc}",
            )

        subsections: List[ExtractedSection] = []
        compute = cloud.list_compute()
        if compute:
            subsections.append(
                ExtractedSection(
                    title="Compute",
                    body="\n".join(f"- {c.name} ({c.kind}, {c.region})" for c in compute),
                    data={"resources": [{"name": c.name, "kind": c.kind, "region": c.region} for c in compute]},
                )
            )
        dbs = cloud.list_databases()
        if dbs:
            subsections.append(
                ExtractedSection(
                    title="Databases",
                    body="\n".join(f"- {d.name} {d.engine} {d.version} ({d.instance_class})" for d in dbs),
                    data={"instances": [{"identifier": d.name, "engine": d.engine, "version": d.version, "class": d.instance_class, "multi_az": d.multi_az} for d in dbs]},
                )
            )
        queues = cloud.list_queues()
        if queues:
            subsections.append(
                ExtractedSection(
                    title="Queues",
                    body="\n".join(f"- {q.name} ({q.kind})" for q in queues),
                    data={"queues": [{"name": q.name, "kind": q.kind} for q in queues]},
                )
            )
        logs = cloud.list_log_groups(top_by_bytes=10)
        if logs:
            subsections.append(
                ExtractedSection(
                    title="Log groups (top 10 by size)",
                    body="\n".join(f"- {g.name} ({g.stored_bytes} bytes, retention={g.retention_days}d)" for g in logs),
                    data={"groups": [{"name": g.name, "stored_bytes": g.stored_bytes, "retention_days": g.retention_days} for g in logs]},
                )
            )

        return ExtractedSection(
            title=f"{cloud.kind.upper()} infrastructure — account {identity.account_id}, region {identity.region}",
            body="Live resource inventory at extract time.",
            subsections=subsections,
        )
