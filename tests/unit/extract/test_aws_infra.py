"""ExtractAwsInfra — the cloud-infra extractor (default cloud=aws).

End-to-end: is_available gating, the UNREACHABLE friendly-degradation
contract when caller_identity fails, the success title carrying
account + region, native AWS subsections via moto, the --aws-extract-service
filter, and the non-AWS (generic walker) branch via a fake cloud.
"""

from __future__ import annotations

import argparse
from typing import Any
from unittest import mock

import boto3
import pytest
from moto import mock_aws

from briar.extract import EXTRACTORS
from briar.extract.aws_infra import ExtractAwsInfra

REGION = "us-east-1"

pytestmark = pytest.mark.boundary


def _args(**over: Any) -> argparse.Namespace:
    base = dict(
        cloud="aws",
        aws_extract_profile=None,
        aws_extract_region=REGION,
        aws_extract_service=[],
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


@pytest.fixture
def moto_env(monkeypatch: Any):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        yield


def test_registered_as_aws_infra() -> None:
    assert isinstance(EXTRACTORS["aws-infra"], ExtractAwsInfra)


# ─── UNREACHABLE contract ────────────────────────────────────────────


def test_unreachable_when_caller_identity_fails() -> None:
    ext = ExtractAwsInfra()
    with mock.patch("boto3.Session") as session_cls:
        session_cls.return_value.client.return_value.get_caller_identity.side_effect = RuntimeError("expired token")
        section = ext.extract(_args())
    assert "UNREACHABLE" in section.title
    assert "AWS infrastructure" in section.title
    assert "expired token" in section.body
    # Friendly degradation: no subsections, no raise.
    assert section.subsections == []


# ─── success path against moto ───────────────────────────────────────


def test_extract_renders_account_region_and_native_subsections(moto_env: Any) -> None:
    session = boto3.Session(region_name=REGION)
    session.client("sqs").create_queue(QueueName="orders")
    rds = session.client("rds")
    rds.create_db_instance(
        DBInstanceIdentifier="primary",
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        EngineVersion="15.4",
        AllocatedStorage=20,
        MasterUsername="admin",
        MasterUserPassword="hunter2hunter2",
    )

    section = ExtractAwsInfra().extract(_args())

    # Title carries the moto STS account id + region.
    assert section.title == f"AWS infrastructure — account 123456789012, region {REGION}"
    titles = [s.title for s in section.subsections]
    assert any(t.startswith("SQS (") for t in titles)
    assert any(t.startswith("RDS (") for t in titles)


def test_aws_extract_service_filter_limits_subsections(moto_env: Any) -> None:
    session = boto3.Session(region_name=REGION)
    session.client("sqs").create_queue(QueueName="orders")

    section = ExtractAwsInfra().extract(_args(aws_extract_service=["sqs"]))

    titles = [s.title for s in section.subsections]
    assert titles == ["SQS (1 queue(s))"]


# ─── is_available ────────────────────────────────────────────────────


def test_is_available_true_with_boto3(moto_env: Any) -> None:
    assert ExtractAwsInfra().is_available(_args()) is True


def test_is_available_false_when_cloud_build_raises() -> None:
    ext = ExtractAwsInfra()
    with mock.patch.object(ext, "_cloud", side_effect=RuntimeError("boom")):
        assert ext.is_available(_args()) is False


# ─── non-AWS branch uses the generic walker (no services= kwarg) ─────


def test_non_aws_cloud_uses_generic_list_subsections() -> None:
    """For a non-AwsCloudProvider the extractor must call
    list_subsections() with NO services kwarg (ISP) and still title the
    section with that cloud's account/region."""
    from briar.extract._cloud import AccountIdentity, CloudProvider, ComputeResource

    class FakeGcp(CloudProvider):
        kind = "gcp"

        def is_available(self) -> bool:
            return True

        def caller_identity(self) -> AccountIdentity:
            return AccountIdentity(account_id="my-project", region="us-central1")

        def list_compute(self):
            return [ComputeResource(name="web", kind="cloud-run", region="us-central1")]

    ext = ExtractAwsInfra()
    with mock.patch.object(ext, "_cloud", return_value=FakeGcp()):
        section = ext.extract(_args(cloud="gcp"))

    assert section.title == "GCP infrastructure — account my-project, region us-central1"
    assert [s.title for s in section.subsections] == ["Compute"]
