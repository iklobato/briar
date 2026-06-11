"""AwsCloudProvider — the vendor adapter over the aws_services gatherers.

Exercised end-to-end against moto so the boto3 session, the per-service
gatherer registry, and the normalisation into CloudProvider dataclasses
(ComputeResource / DatabaseResource / QueueResource / LogGroup) are all
real. We assert the EXACT normalised fields, plus the documented
failure contract: the @swallow_errors decorator on each list_* verb
returns the default ([]) on a botocore ClientError rather than raising.

AWS doc refs:
- sts:GetCallerIdentity →
  https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sts/client/get_caller_identity.html
  (Account / Arn / UserId)
- ECS describe_services / Lambda list_functions / RDS describe_db_instances /
  SQS list_queues / CloudWatch Logs describe_log_groups — see
  test_aws_services.py for the per-field doc links.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from briar.extract._cloud import ComputeResource, DatabaseResource, LogGroup, QueueResource
from briar.extract._clouds import make_cloud
from briar.extract._clouds.aws import AwsCloudProvider

REGION = "us-east-1"

pytestmark = pytest.mark.boundary

_LAMBDA_ZIP = (
    b"PK\x03\x04\x14\x00\x00\x00\x00\x00\x00\x00!\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x08\x00\x00\x00handler.pyPK\x01\x02\x14\x00"
    b"\x14\x00\x00\x00\x00\x00\x00\x00!\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"handler.pyPK\x05\x06\x00\x00\x00\x00\x01\x00\x01\x006\x00\x00\x00"
    b"&\x00\x00\x00\x00\x00"
)
_LAMBDA_TRUST = '{"Version": "2012-10-17", "Statement": [{"Effect": "Allow", ' '"Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}'


@pytest.fixture
def moto_env(monkeypatch: Any):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        yield


@pytest.fixture
def provider(moto_env: Any) -> AwsCloudProvider:
    """A provider whose session is the moto-backed default boto3 chain.

    No company/profile → falls through to the ambient-cred branch in
    _make_session, which under moto is fully intercepted."""
    return AwsCloudProvider(region=REGION)


# ─── is_available / required_env_vars ────────────────────────────────


def test_is_available_true_with_boto3() -> None:
    # boto3 is a hard runtime dep — availability gates only on import.
    assert AwsCloudProvider(region=REGION).is_available() is True


def test_is_available_false_when_boto3_missing(mocker: Any) -> None:
    real_import = __import__

    def fake_import(name: str, *a: Any, **k: Any):
        if name == "boto3":
            raise ImportError("no boto3")
        return real_import(name, *a, **k)

    mocker.patch("builtins.__import__", side_effect=fake_import)
    assert AwsCloudProvider(region=REGION).is_available() is False


def test_required_env_vars_empty_without_company() -> None:
    assert AwsCloudProvider.required_env_vars() == []


def test_required_env_vars_names_per_company() -> None:
    names = AwsCloudProvider.required_env_vars("acme")
    assert names == ["AWS_ACME_ACCESS_KEY_ID", "AWS_ACME_SECRET_ACCESS_KEY"]


# ─── caller_identity ─────────────────────────────────────────────────


def test_caller_identity_returns_account_and_region(provider: AwsCloudProvider) -> None:
    identity = provider.caller_identity()
    # moto's default STS account id.
    assert identity.account_id == "123456789012"
    assert identity.region == REGION


def test_explicit_creds_build_a_keyed_session(monkeypatch: Any) -> None:
    """When AWS_<COMPANY>_* creds are present the provider builds a
    static-keyed session instead of the profile chain. Pin that the
    keys are threaded into boto3.Session(...)."""
    monkeypatch.setenv("AWS_ACME_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_ACME_SECRET_ACCESS_KEY", "secretkey")
    monkeypatch.setenv("AWS_ACME_SESSION_TOKEN", "tok")
    with mock.patch("boto3.Session") as session_cls:
        session_cls.return_value.client.return_value.get_caller_identity.return_value = {"Account": "999"}
        prov = AwsCloudProvider(company="acme", region="eu-west-1")
        identity = prov.caller_identity()
    _, kwargs = session_cls.call_args
    assert kwargs["aws_access_key_id"] == "AKIAEXAMPLE"
    assert kwargs["aws_secret_access_key"] == "secretkey"
    assert kwargs["aws_session_token"] == "tok"
    assert kwargs["region_name"] == "eu-west-1"
    assert identity.account_id == "999"


# ─── list_compute (ECS + Lambda) ─────────────────────────────────────


def _seed_ecs(session: Any) -> None:
    ecs = session.client("ecs")
    ecs.create_cluster(clusterName="prod")
    task_def = ecs.register_task_definition(
        family="web-task",
        containerDefinitions=[{"name": "app", "image": "nginx", "memory": 128}],
    )[
        "taskDefinition"
    ]["taskDefinitionArn"]
    ecs.create_service(cluster="prod", serviceName="web", taskDefinition=task_def, desiredCount=2)


def _seed_lambda(session: Any) -> None:
    iam = session.client("iam")
    role = iam.create_role(RoleName="lr", AssumeRolePolicyDocument=_LAMBDA_TRUST)["Role"]["Arn"]
    session.client("lambda").create_function(
        FunctionName="ingest",
        Runtime="python3.11",
        Role=role,
        Handler="h.h",
        Code={"ZipFile": _LAMBDA_ZIP},
        MemorySize=256,
        Timeout=15,
    )


def test_list_compute_merges_ecs_and_lambda(provider: AwsCloudProvider) -> None:
    session = provider._make_session()
    _seed_ecs(session)
    _seed_lambda(session)

    compute = provider.list_compute()

    assert all(isinstance(c, ComputeResource) for c in compute)
    by_name = {c.name: c for c in compute}
    assert by_name["web"].kind == "ecs-service"
    assert by_name["web"].region == REGION
    assert by_name["ingest"].kind == "lambda"
    # ECS rows come first, then lambda rows.
    kinds = [c.kind for c in compute]
    assert kinds == ["ecs-service", "lambda"]


def test_list_compute_swallows_client_error(provider: AwsCloudProvider) -> None:
    """A ClientError mid-gather must degrade to [] (the @swallow_errors
    default), not propagate. This is the friendly-degradation contract."""
    err = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "ListClusters")
    with mock.patch.object(provider, "_gather_via", side_effect=err):
        assert provider.list_compute() == []


# ─── list_databases (RDS) ────────────────────────────────────────────


def test_list_databases_normalises_rds(provider: AwsCloudProvider) -> None:
    rds = provider._make_session().client("rds")
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

    dbs = provider.list_databases()

    assert len(dbs) == 1
    db = dbs[0]
    assert isinstance(db, DatabaseResource)
    # Regression guard: the adapter reads row["id"], NOT "identifier".
    # The "identifier" read was a real bug that produced empty names.
    assert db.name == "primary"
    assert db.engine == "postgres 15.4"
    assert db.instance_class == "db.t3.medium"
    assert db.multi_az is True
    assert db.region == REGION
    # Regression guard (bug fixed): the adapter previously read
    # row["allocated_gb"], a key the rds gatherer never writes (it emits
    # "storage_gb"), so extra["allocated_gb"] was ALWAYS None in prod.
    assert db.extra == {"allocated_gb": 100}


def test_list_databases_empty_returns_empty_list(provider: AwsCloudProvider) -> None:
    assert provider.list_databases() == []


# ─── list_queues (SQS) ───────────────────────────────────────────────


def test_list_queues_normalises_sqs(provider: AwsCloudProvider) -> None:
    sqs = provider._make_session().client("sqs")
    sqs.create_queue(QueueName="orders")

    queues = provider.list_queues()

    assert len(queues) == 1
    q = queues[0]
    assert isinstance(q, QueueResource)
    assert q.name == "orders"
    assert q.kind == "sqs"
    assert q.region == REGION
    # extra carries the raw gatherer row (url + name).
    assert q.extra["name"] == "orders"
    assert q.extra["url"].endswith("orders")


# ─── list_log_groups ─────────────────────────────────────────────────


def test_list_log_groups_reads_top_log_groups_key(provider: AwsCloudProvider) -> None:
    logs = provider._make_session().client("logs")
    logs.create_log_group(logGroupName="/svc/a")
    logs.put_retention_policy(logGroupName="/svc/a", retentionInDays=30)

    groups = provider.list_log_groups()

    # Regression guard: the adapter reads gatherer.data_key
    # ("top_log_groups"), NOT a hardcoded "groups" that always returned [].
    assert len(groups) == 1
    g = groups[0]
    assert isinstance(g, LogGroup)
    assert g.name == "/svc/a"
    assert g.retention_days == 30


def test_list_log_groups_respects_top_by_bytes_cap(provider: AwsCloudProvider) -> None:
    logs = provider._make_session().client("logs")
    for i in range(5):
        logs.create_log_group(logGroupName=f"/g/{i}")

    assert len(provider.list_log_groups(top_by_bytes=3)) == 3


# ─── list_subsections (native AWS renderer) ──────────────────────────


def test_list_subsections_uses_native_gatherers(provider: AwsCloudProvider) -> None:
    session = provider._make_session()
    _seed_ecs(session)
    session.client("sqs").create_queue(QueueName="orders")

    sections = provider.list_subsections()

    titles = [s.title for s in sections]
    # ECS + SQS produced data.
    assert any(t.startswith("ECS (") for t in titles)
    assert any(t.startswith("SQS (") for t in titles)
    # The native renderer's `is_empty` filter keys on title, NOT body, so
    # the gatherers' "_no instances_" / "_no functions_" placeholders
    # (which have a title) ARE kept — they're never the empty sentinel.
    by_title = {s.title: s for s in sections}
    assert "RDS" in by_title and "_no instances_" in by_title["RDS"].body
    assert "Lambda" in by_title and "_no functions_" in by_title["Lambda"].body
    # The tagging-inventory gatherer also contributes a section. moto's
    # resourcegroupstaggingapi aggregation is unreliable, so it may render
    # either a real "Resource inventory" section or a caught "_skipped_"
    # one — either way it's a non-empty section, so the count is 6.
    assert any(t.startswith("Resource inventory") or t == "TAGGING-INVENTORY" for t in titles)
    # All six registered gatherers render a section.
    assert len(sections) == 6


def test_list_subsections_services_filter(provider: AwsCloudProvider) -> None:
    session = provider._make_session()
    _seed_ecs(session)
    session.client("sqs").create_queue(QueueName="orders")

    sections = provider.list_subsections(services=["ecs"])

    titles = [s.title for s in sections]
    assert any(t.startswith("ECS") for t in titles)
    assert not any(t.startswith("SQS") for t in titles)


def test_list_subsections_per_gatherer_error_isolated(provider: AwsCloudProvider) -> None:
    """One gatherer raising must not kill the others — the failing one is
    rendered as a '_skipped_' section, the rest still gather."""
    session = provider._make_session()
    session.client("sqs").create_queue(QueueName="orders")

    from briar.extract.aws_services import AWS_SERVICE_GATHERERS

    boom = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "ListClusters")
    with mock.patch.object(AWS_SERVICE_GATHERERS["ecs"], "gather", side_effect=boom):
        sections = provider.list_subsections(services=["ecs", "sqs"])

    by_title = {s.title: s for s in sections}
    assert "ECS" in by_title
    assert "_skipped" in by_title["ECS"].body
    assert any(t.startswith("SQS") for t in by_title)


# ─── registry wiring ─────────────────────────────────────────────────


def test_make_cloud_aws_returns_provider() -> None:
    cloud = make_cloud("aws", company="acme", region="us-west-2", profile="prof")
    assert isinstance(cloud, AwsCloudProvider)
    assert cloud.kind == "aws"
    assert cloud._region == "us-west-2"
