"""AWS infrastructure extractor — orchestrates the per-service
gatherers in `aws_services/`.

This file does no service-specific work itself. It opens the boto3
session, calls `sts:GetCallerIdentity` to surface the account number,
then walks `AWS_SERVICE_GATHERERS` (filtered by the user's selection).
Every actual API call lives in its own `aws_services/<svc>.py` module
— Single Responsibility per gatherer."""

from __future__ import annotations

import argparse
from typing import List, Optional

from briar.env_vars import CredEnv
from briar.extract.aws_services import AWS_SERVICE_GATHERERS
from briar.extract.base import ExtractedSection, KnowledgeExtractor


def _build_session(args: argparse.Namespace, boto3):
    """Build a boto3.Session, preferring per-company env vars over the
    profile name. The env-var path is what runs on the headless
    scheduler droplet, where the local AWS profile doesn't exist."""
    profile = getattr(args, "aws_extract_profile", None) or ""
    # YAML's --aws-extract-region is the source of truth; env override
    # only applies when the YAML left it unset (it has a default, so
    # in practice the YAML always wins).
    region = args.aws_extract_region
    key_id = CredEnv.AWS_KEY_ID.read(profile) if profile else None
    secret = CredEnv.AWS_SECRET.read(profile) if profile else None
    if key_id and secret:
        return boto3.Session(
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            aws_session_token=CredEnv.AWS_SESSION.read(profile),
            region_name=region,
        )
    return boto3.Session(
        profile_name=profile or None,
        region_name=region,
    )


class ExtractAwsInfra(KnowledgeExtractor):
    name = "aws-infra"
    description = "delegates to per-service gatherers under aws_services/"
    requires_aws = True

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--aws-extract-profile",
            help="Local AWS profile name (falls back to default boto3 chain)",
        )
        parser.add_argument(
            "--aws-extract-region", default="us-east-1",
            help="AWS region to inspect (default: us-east-1)",
        )
        parser.add_argument(
            "--aws-extract-service", action="append", default=[],
            choices=sorted(AWS_SERVICE_GATHERERS.keys()),
            help="Which AWS services to include (repeatable; default: all)",
        )

    def is_available(self, args: argparse.Namespace) -> bool:
        try:
            import boto3  # noqa: F401
            return True
        except ImportError:
            return False

    def extract(self, args: argparse.Namespace) -> Optional[ExtractedSection]:
        # Lazy import — keeps boto3 off the import path for users who
        # don't run this extractor.
        import boto3

        session = _build_session(args, boto3)

        try:
            acct = session.client("sts").get_caller_identity().get("Account", "?")
        except Exception as exc:  # noqa: BLE001 — surface as note in output
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
            except Exception as exc:  # noqa: BLE001 — each service stays independent
                section = ExtractedSection(
                    title=svc_name.upper(),
                    body=f"_skipped — {exc}_",
                )
            if section is not None:
                subsections.append(section)

        return ExtractedSection(
            title=(
                f"AWS infrastructure — account {acct}, "
                f"region {args.aws_extract_region}"
            ),
            body="Live resource inventory at extract time.",
            subsections=subsections,
        )
