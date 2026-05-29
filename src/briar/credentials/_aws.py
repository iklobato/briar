"""Shared boto3 client factory for the AWS-backed credential stores.

Secrets Manager and SSM Parameter Store build an identical client —
same bounded timeouts + standard-mode retries (the boundary policy
CLAUDE.md mandates). Keep that `Config` in one place so the two stores
can't drift apart. boto3 is imported lazily so importing this module
stays cheap for non-AWS code paths."""

from __future__ import annotations


def boto_client(service: str):
    """Return a boto3 client for `service` with bounded timeouts and
    standard-mode retries. Credentials come from the ambient chain
    (IAM role on EC2, SSO profile locally, env vars)."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        service,
        config=Config(connect_timeout=5, read_timeout=15, retries={"mode": "standard", "max_attempts": 3}),
    )
