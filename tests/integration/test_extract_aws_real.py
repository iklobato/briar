"""End-to-end: `briar extract --include aws-infra` driving the REAL
`ExtractAwsInfra` extractor + REAL `AwsCloudProvider` + REAL boto3 against
``moto``'s ``mock_aws`` — no function-seam mock. The command, provider,
sts caller-identity probe, every per-service gatherer (ECS / RDS / Lambda /
SQS / CloudWatch Logs), the composer, and the on-disk file store all execute;
only the AWS endpoint is faked by moto.

Credentials reach the command exactly the way production does: the per-company
``AWS_<COMPANY>_*`` env vars that ``AwsCloudProvider._make_session`` reads via
``CredEnv`` (``AWS_ACME_ACCESS_KEY_ID`` / ``_SECRET_ACCESS_KEY`` /
``_SESSION_TOKEN``). moto intercepts the signed calls, so the placeholder creds
never leave the process and no network is touched.

AWS API response shapes are moto's faithful emulations of the boto3 contract:
- ECS    list_clusters / list_services / describe_services
         https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ecs/client/describe_services.html
- RDS    describe_db_instances
         https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/rds/client/describe_db_instances.html
- Lambda list_functions
         https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/lambda/client/list_functions.html
- SQS    list_queues
         https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs/client/list_queues.html
- Logs   describe_log_groups
         https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/logs/client/describe_log_groups.html
- STS    get_caller_identity (moto always answers account 123456789012)
         https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sts/client/get_caller_identity.html
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

pytestmark = pytest.mark.integration

# moto's fixed account for an unauthenticated/placeholder caller; it appears in
# the rendered section title via the real sts get_caller_identity() probe.
MOTO_ACCOUNT = "123456789012"

# moto's standard test creds (NOT a real secret; obvious placeholders).
_AWS_ENV = {
    "AWS_ACME_ACCESS_KEY_ID": "testing-not-a-secret",
    "AWS_ACME_SECRET_ACCESS_KEY": "testing-not-a-secret",
    "AWS_ACME_SESSION_TOKEN": "testing-not-a-secret",
}


# ── seeding helpers (real boto3 against moto) ─────────────────────────────

_LAMBDA_ASSUME_ROLE = (
    '{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", ' '"Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}'
)

# Minimal valid zip moto accepts for create_function.
_LAMBDA_ZIP = (
    b"PK\x03\x04\x14\x00\x00\x00\x00\x00\x00\x00!\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x08\x00\x00\x00handler.pyPK\x01\x02\x14\x00"
    b"\x14\x00\x00\x00\x00\x00\x00\x00!\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"handler.pyPK\x05\x06\x00\x00\x00\x00\x01\x00\x01\x006\x00\x00\x00"
    b"&\x00\x00\x00\x00\x00"
)


def _seed_ecs(session: Any, region: str, *, cluster: str, service: str, desired: int) -> None:
    ecs = session.client("ecs", region_name=region)
    ecs.create_cluster(clusterName=cluster)
    task_def = ecs.register_task_definition(
        family=f"{service}-task",
        containerDefinitions=[{"name": "app", "image": "nginx:latest", "memory": 128}],
    )[
        "taskDefinition"
    ]["taskDefinitionArn"]
    ecs.create_service(cluster=cluster, serviceName=service, taskDefinition=task_def, desiredCount=desired)


def _seed_lambda(session: Any, region: str, *, name: str, runtime: str, memory: int, timeout: int) -> None:
    iam = session.client("iam", region_name=region)
    try:
        role_arn = iam.create_role(RoleName="lambda-exec", AssumeRolePolicyDocument=_LAMBDA_ASSUME_ROLE)["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName="lambda-exec")["Role"]["Arn"]
    lam = session.client("lambda", region_name=region)
    lam.create_function(
        FunctionName=name,
        Runtime=runtime,
        Role=role_arn,
        Handler="handler.handler",
        Code={"ZipFile": _LAMBDA_ZIP},
        MemorySize=memory,
        Timeout=timeout,
    )


def _seed_rds(session: Any, region: str, *, ident: str, engine: str, version: str, klass: str, storage: int, multi_az: bool) -> None:
    rds = session.client("rds", region_name=region)
    rds.create_db_instance(
        DBInstanceIdentifier=ident,
        DBInstanceClass=klass,
        Engine=engine,
        EngineVersion=version,
        AllocatedStorage=storage,
        MultiAZ=multi_az,
        MasterUsername="admin",
        MasterUserPassword="hunter2hunter2",
    )


def _seed_sqs(session: Any, region: str, *, name: str) -> None:
    session.client("sqs", region_name=region).create_queue(QueueName=name)


def _read_blob(root: Path) -> str:
    return "\n".join(p.read_text() for p in root.rglob("*") if p.is_file())


# ── happy path: every service seeded, all surfaced on disk ────────────────


def test_extract_aws_infra_happy_path_real(cli, tmp_root, monkeypatch) -> None:
    """Seed ECS + Lambda + SQS + RDS + Logs in moto, run the real command,
    and assert the on-disk blob reflects the EXACT seeded names/counts/region
    — values that can only come from the resources we created."""
    for k, v in _AWS_ENV.items():
        monkeypatch.setenv(k, v)

    region = "eu-west-1"
    root = tmp_root / "knowledge"

    with mock_aws():
        session = boto3.Session(region_name=region)
        _seed_ecs(session, region, cluster="prod", service="web", desired=3)
        _seed_lambda(session, region, name="ingest", runtime="python3.11", memory=512, timeout=30)
        _seed_rds(session, region, ident="primary", engine="postgres", version="15.4", klass="db.t3.medium", storage=100, multi_az=True)
        _seed_sqs(session, region, name="orders")
        session.client("logs", region_name=region).create_log_group(logGroupName="/aws/lambda/ingest")

        result = cli(
            "extract",
            "--company",
            "acme",
            "--include",
            "aws-infra",
            "--aws-extract-region",
            region,
            "--storage",
            "file",
            "--root",
            str(root),
        )

    assert result.code == 0, result.err
    blob = _read_blob(root)

    # sts caller_identity() drove the title: moto's account + our --region.
    assert f"## AWS infrastructure — account {MOTO_ACCOUNT}, region {region}" in blob

    # ECS: one service "prod/web", desired=3, moto reports running=0.
    assert "### ECS (1 service(s))" in blob
    assert "- prod/web  task=web-task:1  running=0/3" in blob

    # RDS: postgres 15.4, db.t3.medium, 100GB, Multi-AZ flag rendered.
    assert "### RDS (1 instance(s))" in blob
    assert "- primary  postgres 15.4  db.t3.medium  100GB  Multi-AZ" in blob

    # Lambda: name/runtime/memory/timeout.
    assert "### Lambda (1 function(s))" in blob
    assert "- ingest  python3.11  mem=512MB  timeout=30s" in blob

    # SQS: queue name from the last URL path segment.
    assert "### SQS (1 queue(s))" in blob
    assert "- orders" in blob

    # CloudWatch Logs: one group surfaced (moto reports storedBytes=0 → 0MB).
    assert "### CloudWatch Logs (top 1 by size, of 1)" in blob
    assert "- /aws/lambda/ingest  0MB  retention=Noned" in blob


# ── --aws-extract-service filter: only the chosen service is gathered ──────


def test_extract_aws_infra_service_filter_real(cli, tmp_root, monkeypatch) -> None:
    """The repeatable --aws-extract-service flag must restrict gathering to the
    named services. Seed both RDS and SQS but ask only for rds; SQS must be
    absent from the blob (a dropped filter would surface it and fail)."""
    for k, v in _AWS_ENV.items():
        monkeypatch.setenv(k, v)

    region = "us-east-1"
    root = tmp_root / "knowledge"

    with mock_aws():
        session = boto3.Session(region_name=region)
        _seed_rds(session, region, ident="primary", engine="postgres", version="15.4", klass="db.t3.medium", storage=100, multi_az=False)
        _seed_sqs(session, region, name="orders")

        result = cli(
            "extract",
            "--company",
            "acme",
            "--include",
            "aws-infra",
            "--aws-extract-service",
            "rds",
            "--storage",
            "file",
            "--root",
            str(root),
        )

    assert result.code == 0, result.err
    blob = _read_blob(root)
    assert "### RDS (1 instance(s))" in blob
    assert "- primary  postgres 15.4" in blob
    # The SQS gatherer was filtered out entirely — no SQS section at all.
    assert "SQS" not in blob
    assert "orders" not in blob


# ── empty: creds resolve, sts answers, but no resources exist ─────────────


def test_extract_aws_infra_empty_real(cli, tmp_root, monkeypatch) -> None:
    """No seeded resources. The top-level section still renders (sts gives an
    account/region title), and every gatherer reports its empty sentinel — so
    the command writes a blob with the title + all five "_no _" subsections
    rather than failing the 'nothing extracted' guard."""
    for k, v in _AWS_ENV.items():
        monkeypatch.setenv(k, v)

    region = "us-east-1"
    root = tmp_root / "knowledge"

    with mock_aws():
        result = cli(
            "extract",
            "--company",
            "acme",
            "--include",
            "aws-infra",
            "--storage",
            "file",
            "--root",
            str(root),
        )

    assert result.code == 0, result.err
    blob = _read_blob(root)
    assert f"## AWS infrastructure — account {MOTO_ACCOUNT}, region {region}" in blob
    # Each gatherer's empty-sentinel body is present; no rows leaked in.
    assert "### ECS\n\n_no services_" in blob
    assert "### RDS\n\n_no instances_" in blob
    assert "### Lambda\n\n_no functions_" in blob
    assert "### SQS\n\n_no queues_" in blob
    assert "### CloudWatch Logs\n\n_no groups_" in blob


# ── UNREACHABLE: credential/identity failure degrades, never crashes ──────


def test_extract_aws_infra_unreachable_real(cli, tmp_root, monkeypatch) -> None:
    """Credential-failure degradation contract. With NO per-company creds set,
    the provider falls back to a local profile named after the company
    ("acme"), which does not exist → boto3 raises ProfileNotFound from inside
    the real caller_identity() probe. The extractor must catch it and emit the
    friendly UNREACHABLE section, not propagate a crash.

    ProfileNotFound is raised during session construction (before any signing
    or socket), so this path touches no network even outside moto. We pin the
    boto3 config/credential files to nonexistent paths so a developer's local
    ~/.aws/config can never accidentally define an 'acme' profile and mask the
    failure."""
    # env_sandbox already strips AWS_*; assert no per-company creds are set so
    # the static-cred branch is skipped and the profile fallback is taken.
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent/config")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent/credentials")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    root = tmp_root / "knowledge"

    result = cli(
        "extract",
        "--company",
        "acme",
        "--include",
        "aws-infra",
        "--storage",
        "file",
        "--root",
        str(root),
    )

    assert result.code == 0, result.err
    blob = _read_blob(root)
    # The friendly degradation section, keyed off the real exception text.
    assert "## AWS infrastructure (UNREACHABLE)" in blob
    assert "Could not resolve caller identity —" in blob
    assert "acme" in blob  # the missing-profile name surfaces in the reason
    # And it did NOT render any resource subsections or a healthy title.
    assert "account 123456789012" not in blob
    assert "### ECS" not in blob
