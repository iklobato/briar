"""AWS per-service gatherer contracts, exercised against moto.

Each gatherer (ECS / Lambda / Logs / SQS / RDS) takes a real boto3
``Session`` and returns one ``ExtractedSection``. We seed moto with a
realistic AWS API surface and assert the EXACT normalised rows/counts
the gatherer produces — an off-by-one in a count, a swapped field, or
a dropped row would fail these.

Response shapes modelled from the boto3 docs:
- ECS: list_clusters / list_services / describe_services →
  https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ecs/client/describe_services.html
  (services[].serviceName / desiredCount / runningCount / taskDefinition)
- Lambda: list_functions →
  https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/lambda/client/list_functions.html
  (Functions[].FunctionName / Runtime / MemorySize / Timeout / LastModified)
- CloudWatch Logs: describe_log_groups →
  https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/logs/client/describe_log_groups.html
  (logGroups[].logGroupName / storedBytes / retentionInDays)
- SQS: list_queues →
  https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs/client/list_queues.html
  (QueueUrls[])
- RDS: describe_db_instances →
  https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/rds/client/describe_db_instances.html
  (DBInstances[].DBInstanceIdentifier / Engine / EngineVersion / DBInstanceClass / AllocatedStorage / MultiAZ)
"""

from __future__ import annotations

from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from briar.extract.aws_services import AWS_SERVICE_GATHERERS

REGION = "us-east-1"

pytestmark = pytest.mark.boundary


@pytest.fixture
def aws_session(monkeypatch: Any):
    """A boto3 Session pinned to a moto-backed region with dummy creds.

    moto intercepts every AWS call so nothing hits the network. The
    autouse ``env_sandbox`` fixture strips AWS_* env vars, so we set
    fake static creds here to keep botocore's credential chain happy."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        yield boto3.Session(region_name=REGION)


# ─── ECS ──────────────────────────────────────────────────────────────


def _create_ecs_service(client: Any, *, cluster: str, service: str, desired: int) -> None:
    client.create_cluster(clusterName=cluster)
    task_def = client.register_task_definition(
        family=f"{service}-task",
        containerDefinitions=[{"name": "app", "image": "nginx:latest", "memory": 128}],
    )[
        "taskDefinition"
    ]["taskDefinitionArn"]
    client.create_service(
        cluster=cluster,
        serviceName=service,
        taskDefinition=task_def,
        desiredCount=desired,
    )


def test_ecs_normalises_services_across_clusters(aws_session: Any) -> None:
    ecs = aws_session.client("ecs")
    _create_ecs_service(ecs, cluster="prod", service="web", desired=3)
    _create_ecs_service(ecs, cluster="staging", service="worker", desired=1)

    section = AWS_SERVICE_GATHERERS["ecs"].gather(aws_session)

    rows = section.data["services"]
    assert len(rows) == 2
    assert section.title == "ECS (2 service(s))"
    by_name = {r["name"]: r for r in rows}
    assert by_name["web"]["cluster"] == "prod"
    assert by_name["web"]["desired"] == 3
    assert by_name["web"]["task_def"] == "web-task:1"
    assert by_name["worker"]["cluster"] == "staging"
    assert by_name["worker"]["desired"] == 1
    # body renders running/desired — moto reports runningCount=0 for a
    # service with no running tasks.
    assert "prod/web" in section.body
    assert "running=0/3" in section.body


def test_ecs_skips_clusters_with_no_services(aws_session: Any) -> None:
    ecs = aws_session.client("ecs")
    ecs.create_cluster(clusterName="empty")
    _create_ecs_service(ecs, cluster="prod", service="api", desired=2)

    section = AWS_SERVICE_GATHERERS["ecs"].gather(aws_session)

    # The empty cluster contributes nothing; only the one real service.
    assert len(section.data["services"]) == 1
    assert section.data["services"][0]["name"] == "api"


def test_ecs_empty_when_no_clusters(aws_session: Any) -> None:
    section = AWS_SERVICE_GATHERERS["ecs"].gather(aws_session)
    assert section.title == "ECS"
    assert "no services" in section.body
    assert section.data == {}


# ─── Lambda ───────────────────────────────────────────────────────────

# Minimal valid zip for moto's lambda create_function.
_LAMBDA_ZIP = (
    b"PK\x03\x04\x14\x00\x00\x00\x00\x00\x00\x00!\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x08\x00\x00\x00handler.pyPK\x01\x02\x14\x00"
    b"\x14\x00\x00\x00\x00\x00\x00\x00!\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"handler.pyPK\x05\x06\x00\x00\x00\x00\x01\x00\x01\x006\x00\x00\x00"
    b"&\x00\x00\x00\x00\x00"
)


_LAMBDA_ASSUME_ROLE = (
    '{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", ' '"Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}'
)


def _lambda_role_arn(session: Any) -> str:
    """moto validates that a lambda's role is assumable by Lambda, so we
    create a real IAM role with the lambda trust policy first."""
    iam = session.client("iam")
    try:
        role = iam.create_role(RoleName="lambda-exec", AssumeRolePolicyDocument=_LAMBDA_ASSUME_ROLE)
        return role["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        return iam.get_role(RoleName="lambda-exec")["Role"]["Arn"]


def _create_lambda(client: Any, *, role_arn: str, name: str, runtime: str, memory: int, timeout: int) -> None:
    client.create_function(
        FunctionName=name,
        Runtime=runtime,
        Role=role_arn,
        Handler="handler.handler",
        Code={"ZipFile": _LAMBDA_ZIP},
        MemorySize=memory,
        Timeout=timeout,
    )


def test_lambda_normalises_functions(aws_session: Any) -> None:
    lam = aws_session.client("lambda")
    role = _lambda_role_arn(aws_session)
    _create_lambda(lam, role_arn=role, name="ingest", runtime="python3.11", memory=512, timeout=30)
    _create_lambda(lam, role_arn=role, name="notify", runtime="python3.12", memory=128, timeout=10)

    section = AWS_SERVICE_GATHERERS["lambda"].gather(aws_session)

    rows = section.data["functions"]
    assert len(rows) == 2
    assert section.title == "Lambda (2 function(s))"
    by_name = {r["name"]: r for r in rows}
    assert by_name["ingest"]["runtime"] == "python3.11"
    assert by_name["ingest"]["memory_mb"] == 512
    assert by_name["ingest"]["timeout_s"] == 30
    assert by_name["notify"]["memory_mb"] == 128
    assert "ingest" in section.body
    assert "mem=512MB" in section.body


def test_lambda_paginates_many_functions(aws_session: Any) -> None:
    """The gatherer drives a get_paginator — assert it walks past the
    first page. moto paginates list_functions; create enough functions
    that the default page size (50) truncates so NextMarker is exercised."""
    lam = aws_session.client("lambda")
    role = _lambda_role_arn(aws_session)
    expected = set()
    for i in range(60):
        name = f"fn-{i:03d}"
        _create_lambda(lam, role_arn=role, name=name, runtime="python3.11", memory=128, timeout=3)
        expected.add(name)

    section = AWS_SERVICE_GATHERERS["lambda"].gather(aws_session)

    names = {r["name"] for r in section.data["functions"]}
    assert names == expected
    assert len(section.data["functions"]) == 60


def test_lambda_empty_when_no_functions(aws_session: Any) -> None:
    section = AWS_SERVICE_GATHERERS["lambda"].gather(aws_session)
    assert section.title == "Lambda"
    assert "no functions" in section.body


# ─── CloudWatch Logs ──────────────────────────────────────────────────


def test_logs_sorts_top_by_stored_bytes(aws_session: Any) -> None:
    logs = aws_session.client("logs")
    # moto doesn't let you set storedBytes directly, so seed groups then
    # patch storedBytes via the describe path. moto returns storedBytes=0,
    # which still exercises the sort + render path deterministically.
    for name in ("/aws/lambda/a", "/aws/lambda/b", "/ecs/c"):
        logs.create_log_group(logGroupName=name)
    logs.put_retention_policy(logGroupName="/aws/lambda/a", retentionInDays=14)

    section = AWS_SERVICE_GATHERERS["logs"].gather(aws_session)

    rows = section.data["top_log_groups"]
    assert len(rows) == 3
    assert section.title == "CloudWatch Logs (top 3 by size, of 3)"
    by_name = {r["name"]: r for r in rows}
    assert by_name["/aws/lambda/a"]["retention_days"] == 14
    # Group without a retention policy → retentionInDays absent → None.
    assert by_name["/ecs/c"]["retention_days"] is None


def test_logs_caps_at_top_ten_but_reports_total(aws_session: Any) -> None:
    logs = aws_session.client("logs")
    for i in range(13):
        logs.create_log_group(logGroupName=f"/grp/{i:02d}")

    section = AWS_SERVICE_GATHERERS["logs"].gather(aws_session)

    assert len(section.data["top_log_groups"]) == 10
    assert section.title == "CloudWatch Logs (top 10 by size, of 13)"


def test_logs_empty_when_no_groups(aws_session: Any) -> None:
    section = AWS_SERVICE_GATHERERS["logs"].gather(aws_session)
    assert section.title == "CloudWatch Logs"
    assert "no groups" in section.body


# ─── SQS ──────────────────────────────────────────────────────────────


def test_sqs_normalises_queue_names_from_urls(aws_session: Any) -> None:
    sqs = aws_session.client("sqs")
    sqs.create_queue(QueueName="orders")
    sqs.create_queue(QueueName="events.fifo", Attributes={"FifoQueue": "true"})

    section = AWS_SERVICE_GATHERERS["sqs"].gather(aws_session)

    rows = section.data["queues"]
    assert len(rows) == 2
    assert section.title == "SQS (2 queue(s))"
    names = {r["name"] for r in rows}
    # The gatherer takes the last URL path segment as the name.
    assert names == {"orders", "events.fifo"}
    for r in rows:
        assert r["url"].endswith(r["name"])


def test_sqs_empty_when_no_queues(aws_session: Any) -> None:
    section = AWS_SERVICE_GATHERERS["sqs"].gather(aws_session)
    assert section.title == "SQS"
    assert "no queues" in section.body
    assert section.data == {}


# ─── RDS ──────────────────────────────────────────────────────────────


def test_rds_normalises_instances(aws_session: Any) -> None:
    rds = aws_session.client("rds")
    rds.create_db_instance(
        DBInstanceIdentifier="primary",
        DBInstanceClass="db.t3.medium",
        Engine="postgres",
        EngineVersion="15.4",
        AllocatedStorage=100,
        MultiAZ=True,
        MasterUsername="admin",
        MasterUserPassword="hunter2hunter2",
    )
    rds.create_db_instance(
        DBInstanceIdentifier="replica",
        DBInstanceClass="db.t3.small",
        Engine="mysql",
        EngineVersion="8.0",
        AllocatedStorage=20,
        MasterUsername="admin",
        MasterUserPassword="hunter2hunter2",
    )

    section = AWS_SERVICE_GATHERERS["rds"].gather(aws_session)

    rows = section.data["instances"]
    assert len(rows) == 2
    assert section.title == "RDS (2 instance(s))"
    by_id = {r["id"]: r for r in rows}
    assert by_id["primary"]["engine"] == "postgres 15.4"
    assert by_id["primary"]["class"] == "db.t3.medium"
    assert by_id["primary"]["storage_gb"] == 100
    assert by_id["primary"]["multi_az"] is True
    assert by_id["replica"]["multi_az"] is False
    assert "primary" in section.body
    assert "Multi-AZ" in section.body


def test_rds_empty_when_no_instances(aws_session: Any) -> None:
    section = AWS_SERVICE_GATHERERS["rds"].gather(aws_session)
    assert section.title == "RDS"
    assert "no instances" in section.body


# ─── Failure modes: ClientError propagates out of the gatherer ────────
#
# The gatherers themselves do NOT swallow botocore errors — the
# swallowing happens one layer up in AwsCloudProvider via
# @swallow_errors / list_subsections' try/except. Pin that contract so a
# refactor that accidentally adds a bare except inside a gatherer would
# fail here.


def _client_error(op: str, code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "denied"}}, op)


def test_ecs_propagates_access_denied(monkeypatch: Any) -> None:
    from unittest import mock

    session = mock.MagicMock()
    session.client.return_value.list_clusters.side_effect = _client_error("ListClusters", "AccessDeniedException")

    with pytest.raises(ClientError) as ctx:
        AWS_SERVICE_GATHERERS["ecs"].gather(session)
    assert ctx.value.response["Error"]["Code"] == "AccessDeniedException"


def test_rds_propagates_throttling() -> None:
    from unittest import mock

    session = mock.MagicMock()
    session.client.return_value.describe_db_instances.side_effect = _client_error("DescribeDBInstances", "ThrottlingException")

    with pytest.raises(ClientError) as ctx:
        AWS_SERVICE_GATHERERS["rds"].gather(session)
    assert ctx.value.response["Error"]["Code"] == "ThrottlingException"


# ─── Resource Groups Tagging inventory ────────────────────────────────
#
# Driven against a controlled paginator rather than moto: moto's
# resourcegroupstaggingapi coverage is uneven across services, so a fake
# get_resources paginator pins the EXACT normalisation (ARN parsing,
# per-service counts, terse body, full data) deterministically.


def _tagging_session(pages: Any) -> Any:
    """A MagicMock session whose `get_resources` paginator yields `pages`."""
    from unittest import mock

    session = mock.MagicMock()
    session.client.return_value.get_paginator.return_value.paginate.return_value = pages
    return session


def _mapping(arn: str, **tags: str) -> dict:
    return {"ResourceARN": arn, "Tags": [{"Key": k, "Value": v} for k, v in tags.items()]}


def test_tagging_inventory_is_registered() -> None:
    assert "tagging-inventory" in AWS_SERVICE_GATHERERS
    assert AWS_SERVICE_GATHERERS["tagging-inventory"].data_key == "resources"


def test_tagging_normalises_arns_and_counts_by_service() -> None:
    pages = [
        {
            "ResourceTagMappingList": [
                _mapping("arn:aws:sqs:us-east-1:111:orders", app="web"),
                _mapping("arn:aws:sqs:us-east-1:111:events.fifo"),
                _mapping("arn:aws:rds:us-east-1:111:db:primary", env="prod"),
                _mapping("arn:aws:s3:::my-bucket"),
            ]
        }
    ]
    section = AWS_SERVICE_GATHERERS["tagging-inventory"].gather(_tagging_session(pages))

    rows = section.data["resources"]
    assert len(rows) == 4
    assert section.title == "Resource inventory (4 tagged resource(s), 3 service(s))"

    by_name = {r["name"]: r for r in rows}
    # bare resource → no type
    assert by_name["orders"]["service"] == "sqs"
    assert by_name["orders"]["type"] == ""
    assert by_name["orders"]["tags"] == {"app": "web"}
    # "type:id" resource form → split into type + name
    assert by_name["primary"]["service"] == "rds"
    assert by_name["primary"]["type"] == "db"
    # S3 ARN has empty region/account segments
    assert by_name["my-bucket"]["service"] == "s3"
    assert by_name["my-bucket"]["region"] == ""

    # body is per-service counts only, sorted — terse and prompt-safe.
    assert section.body == "- rds: 1\n- s3: 1\n- sqs: 2"
    # full detail must NOT leak into the body (that goes in the prompt blob).
    assert "arn:aws" not in section.body


def test_tagging_walks_every_page() -> None:
    pages = [
        {"ResourceTagMappingList": [_mapping(f"arn:aws:sqs:us-east-1:111:q{i}") for i in range(100)]},
        {"ResourceTagMappingList": [_mapping(f"arn:aws:sqs:us-east-1:111:q{i}") for i in range(100, 150)]},
    ]
    section = AWS_SERVICE_GATHERERS["tagging-inventory"].gather(_tagging_session(pages))

    assert len(section.data["resources"]) == 150
    assert section.title == "Resource inventory (150 tagged resource(s), 1 service(s))"


def test_tagging_empty_when_no_resources() -> None:
    section = AWS_SERVICE_GATHERERS["tagging-inventory"].gather(_tagging_session([{"ResourceTagMappingList": []}]))
    assert section.title == "Resource inventory"
    assert "no tagged resources" in section.body
    assert section.data == {}


def test_tagging_propagates_access_denied() -> None:
    from unittest import mock

    session = mock.MagicMock()
    session.client.return_value.get_paginator.return_value.paginate.side_effect = _client_error("GetResources", "AccessDeniedException")

    with pytest.raises(ClientError) as ctx:
        AWS_SERVICE_GATHERERS["tagging-inventory"].gather(session)
    assert ctx.value.response["Error"]["Code"] == "AccessDeniedException"
