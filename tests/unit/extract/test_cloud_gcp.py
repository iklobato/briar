"""GcpCloudProvider — mocked at the google-cloud client seam.

The provider lazy-imports each Google lib via ``_try_import`` and
instantiates the client with no positional args (ADC supplies auth).
We patch ``_try_import`` to hand back a fake module whose client classes
yield fake resources shaped like the real google-cloud responses, then
assert the normalised CloudProvider dataclasses.

GCP API shape refs:
- Cloud Run v2 Services.list →
  https://cloud.google.com/run/docs/reference/rest/v2/projects.locations.services/list
  (services[].name is "projects/P/locations/L/services/NAME")
- Cloud SQL Admin instances.list →
  https://cloud.google.com/sql/docs/postgres/admin-api/rest/v1/instances/list
  (items[].name / databaseVersion / settings.tier / region / settings.availabilityType)
- Pub/Sub Publisher.list_topics →
  https://cloud.google.com/pubsub/docs/reference/rest/v1/projects.topics/list
  (topics[].name is "projects/P/topics/NAME")
- Cloud Logging buckets.list →
  https://cloud.google.com/logging/docs/reference/v2/rest/v2/projects.locations.buckets/list
  (buckets[].name / retentionDays)
"""

from __future__ import annotations

import types
from typing import Any
from unittest import mock

import pytest

from briar.extract._cloud import ComputeResource, DatabaseResource, LogGroup, QueueResource
from briar.extract._clouds import make_cloud
from briar.extract._clouds.gcp import GcpCloudProvider

PROJECT = "acme-prod"

pytestmark = pytest.mark.boundary


@pytest.fixture
def provider() -> GcpCloudProvider:
    return GcpCloudProvider(profile=PROJECT, region="us-central1")


def _module_with(**attrs: Any) -> types.SimpleNamespace:
    return types.SimpleNamespace(**attrs)


def _obj(**kw: Any) -> Any:
    return types.SimpleNamespace(**kw)


# ─── is_available ────────────────────────────────────────────────────


def test_is_available_false_without_project() -> None:
    assert GcpCloudProvider().is_available() is False


def test_is_available_true_with_project_and_auth(mocker: Any) -> None:
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=object())
    assert GcpCloudProvider(profile=PROJECT).is_available() is True


def test_is_available_false_when_auth_lib_missing(mocker: Any) -> None:
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=None)
    assert GcpCloudProvider(profile=PROJECT).is_available() is False


# ─── caller_identity ─────────────────────────────────────────────────


def test_caller_identity_verifies_adc_and_returns_project(provider: GcpCloudProvider, mocker: Any) -> None:
    google_auth = mock.MagicMock()
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=google_auth)

    identity = provider.caller_identity()

    assert identity.account_id == PROJECT
    assert identity.region == "us-central1"
    # The project IS the identity, but ADC is still probed to fail fast.
    google_auth.default.assert_called_once_with()


def test_caller_identity_raises_when_auth_lib_missing(provider: GcpCloudProvider, mocker: Any) -> None:
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=None)
    with pytest.raises(RuntimeError) as ctx:
        provider.caller_identity()
    assert "briar-cli[gcp]" in str(ctx.value)


def test_caller_identity_propagates_adc_failure(provider: GcpCloudProvider, mocker: Any) -> None:
    """caller_identity is NOT decorated with @swallow_errors — an ADC
    failure must surface so the extractor renders the UNREACHABLE
    section. (google.auth raises DefaultCredentialsError.)"""
    google_auth = mock.MagicMock()
    google_auth.default.side_effect = RuntimeError("Could not automatically determine credentials")
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=google_auth)

    with pytest.raises(RuntimeError):
        provider.caller_identity()


# ─── list_compute (Cloud Run) ────────────────────────────────────────


def test_list_compute_normalises_cloud_run(provider: GcpCloudProvider, mocker: Any) -> None:
    client = mock.MagicMock()
    client.list_services.return_value = [
        _obj(name=f"projects/{PROJECT}/locations/us-central1/services/web"),
        _obj(name=f"projects/{PROJECT}/locations/us-central1/services/api"),
    ]
    module = _module_with(ServicesClient=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=module)

    compute = provider.list_compute()

    assert all(isinstance(c, ComputeResource) for c in compute)
    # The full resource path is reduced to the bare service name.
    assert [c.name for c in compute] == ["web", "api"]
    assert compute[0].kind == "cloud-run"
    assert compute[0].region == "us-central1"
    client.list_services.assert_called_once_with(parent=f"projects/{PROJECT}/locations/us-central1")


def test_list_compute_empty_when_lib_missing(provider: GcpCloudProvider, mocker: Any) -> None:
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=None)
    assert provider.list_compute() == []


def test_list_compute_swallows_runtime_error(provider: GcpCloudProvider, mocker: Any) -> None:
    client = mock.MagicMock()
    client.list_services.side_effect = RuntimeError("403 Permission denied")
    module = _module_with(ServicesClient=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=module)

    assert provider.list_compute() == []


# ─── list_databases (Cloud SQL — paginated discovery API) ────────────


def _sqladmin_module(pages: list[dict[str, Any]]) -> types.SimpleNamespace:
    """Build a fake googleapiclient.discovery module whose
    instances().list().execute() walks `pages` via list_next."""
    state = {"i": 0}
    instances_resource = mock.MagicMock()

    def make_request(page_index: int) -> Any:
        req = mock.MagicMock(name=f"request-{page_index}")
        req.execute.return_value = pages[page_index]
        return req

    requests = [make_request(i) for i in range(len(pages))]

    def list_(**kwargs: Any) -> Any:
        return requests[0]

    def list_next(previous_request: Any, previous_response: Any) -> Any:
        state["i"] += 1
        if state["i"] < len(requests):
            return requests[state["i"]]
        return None

    instances_resource.list.side_effect = list_
    instances_resource.list_next.side_effect = list_next
    service = mock.MagicMock()
    service.instances.return_value = instances_resource
    build = mock.MagicMock(return_value=service)
    return _module_with(build=build)


def test_list_databases_paginates_cloud_sql(provider: GcpCloudProvider, mocker: Any) -> None:
    page1 = {
        "items": [
            {
                "name": "pg-prod",
                "databaseVersion": "POSTGRES_15",
                "region": "us-central1",
                "settings": {"tier": "db-custom-2-7680", "availabilityType": "REGIONAL"},
            }
        ]
    }
    page2 = {
        "items": [
            {
                "name": "mysql-dev",
                "databaseVersion": "MYSQL_8_0",
                "region": "us-east1",
                "settings": {"tier": "db-f1-micro", "availabilityType": "ZONAL"},
            }
        ]
    }
    module = _sqladmin_module([page1, page2])
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=module)

    dbs = provider.list_databases()

    assert all(isinstance(d, DatabaseResource) for d in dbs)
    by_name = {d.name: d for d in dbs}
    # databaseVersion "POSTGRES_15" → engine "postgres", version preserved.
    assert by_name["pg-prod"].engine == "postgres"
    assert by_name["pg-prod"].version == "POSTGRES_15"
    assert by_name["pg-prod"].instance_class == "db-custom-2-7680"
    # availabilityType REGIONAL → multi_az True; ZONAL → False.
    assert by_name["pg-prod"].multi_az is True
    assert by_name["mysql-dev"].engine == "mysql"
    assert by_name["mysql-dev"].multi_az is False
    assert by_name["mysql-dev"].region == "us-east1"


def test_list_databases_empty_items(provider: GcpCloudProvider, mocker: Any) -> None:
    module = _sqladmin_module([{"items": []}])
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=module)
    assert provider.list_databases() == []


# ─── list_queues (Pub/Sub) ───────────────────────────────────────────


def test_list_queues_normalises_pubsub_topics(provider: GcpCloudProvider, mocker: Any) -> None:
    publisher = mock.MagicMock()
    publisher.list_topics.return_value = [
        _obj(name=f"projects/{PROJECT}/topics/orders"),
        _obj(name=f"projects/{PROJECT}/topics/events"),
    ]
    module = _module_with(PublisherClient=mock.MagicMock(return_value=publisher))
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=module)

    queues = provider.list_queues()

    assert all(isinstance(q, QueueResource) for q in queues)
    assert [q.name for q in queues] == ["orders", "events"]
    assert queues[0].kind == "pubsub-topic"
    publisher.list_topics.assert_called_once_with(request={"project": f"projects/{PROJECT}"})


# ─── list_log_groups (Cloud Logging buckets) ─────────────────────────


def test_list_log_groups_normalises_buckets(provider: GcpCloudProvider, mocker: Any) -> None:
    client = mock.MagicMock()
    client.list_buckets.return_value = [
        _obj(name="_Default", retention_days=30),
        _obj(name="_Required", retention_days=400),
    ]
    module = _module_with(Client=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=module)

    groups = provider.list_log_groups()

    assert all(isinstance(g, LogGroup) for g in groups)
    assert [g.name for g in groups] == ["_Default", "_Required"]
    assert groups[1].retention_days == 400
    # GCP doesn't expose per-bucket stored_bytes via this API → 0.
    assert groups[0].stored_bytes == 0
    client.list_buckets.assert_called_once_with(parent=f"projects/{PROJECT}/locations/-")


def test_list_log_groups_caps_at_top_by_bytes(provider: GcpCloudProvider, mocker: Any) -> None:
    client = mock.MagicMock()
    client.list_buckets.return_value = [_obj(name=f"b{i}", retention_days=30) for i in range(5)]
    module = _module_with(Client=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.gcp._try_import", return_value=module)

    assert len(provider.list_log_groups(top_by_bytes=2)) == 2


# ─── registry wiring ─────────────────────────────────────────────────


def test_make_cloud_gcp_returns_provider() -> None:
    cloud = make_cloud("gcp", profile=PROJECT, region="europe-west1")
    assert isinstance(cloud, GcpCloudProvider)
    assert cloud.kind == "gcp"
    assert cloud._project_id == PROJECT
    assert cloud._region == "europe-west1"
