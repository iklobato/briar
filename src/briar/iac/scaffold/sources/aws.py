"""AWS source template.

Family: `cloud`. Read-only context — the agent uses it to query AWS
resource state (instance lists, logs, IAM policies, …) but doesn't take
write actions through tools. The credential is an AWS-role binding
that the backend resolves via STS AssumeRole (external-id pattern).

Mirroring the same shape would let GCP / Azure plug in as siblings."""

from __future__ import annotations

import argparse
from typing import Any, Dict

from briar.iac.scaffold.sources.base import SourceTemplate


class SourceAws(SourceTemplate):
    kind = "aws"
    family = "cloud"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--aws-role-arn",
            help="IAM role ARN the worker assumes to read AWS resources",
        )
        parser.add_argument(
            "--aws-external-id",
            help="External-id required by the trust policy on --aws-role-arn",
        )
        parser.add_argument(
            "--aws-region",
            default="us-east-1",
            help="Default AWS region for resource queries",
        )
        parser.add_argument(
            "--aws-services",
            action="append",
            default=[],
            help="Which AWS services to gather (ec2, s3, iam, logs, …)",
        )

    def build_source(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> Dict[str, Any]:
        services = getattr(args, "aws_services", []) or ["ec2", "iam", "logs"]
        config: Dict[str, Any] = {
            "region": getattr(args, "aws_region", "us-east-1"),
            "services": services,
        }
        binding: Dict[str, Any] = {"kind": "aws_role_chain"}
        role_arn = getattr(args, "aws_role_arn", None)
        external_id = getattr(args, "aws_external_id", None)
        if role_arn:
            binding["role_arn"] = role_arn
        if external_id:
            binding["external_id"] = external_id
        return {
            "key": f"{key_prefix}-aws",
            "name": f"{key_prefix}-aws",
            "kind": "aws",
            "config": config,
            "credentials_ref": None,
            "credential_binding": binding,
        }

    # build_tools inherits the empty default — AWS is read-only here.
