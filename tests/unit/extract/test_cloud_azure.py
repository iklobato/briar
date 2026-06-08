"""AzureCloudProvider — mocked at the azure-mgmt client seam.

The provider lazily ``importlib.import_module``s each azure-mgmt-* lib
via the module-level ``_try_import`` helper. We patch ``_try_import`` to
hand back a fake module whose client classes return fake resource
objects shaped like the real Azure SDK models, then assert the
normalised CloudProvider dataclasses.

Azure REST model refs (the python SDK mirrors these shapes):
- Subscriptions - Get →
  https://learn.microsoft.com/en-us/rest/api/resources/subscriptions/get
  (subscriptionId / displayName)
- Container Apps - List By Subscription →
  https://learn.microsoft.com/en-us/rest/api/containerapps/container-apps/list-by-subscription
  (value[].name / location)
- PostgreSQL Flexible Servers - List →
  https://learn.microsoft.com/en-us/rest/api/postgresql/flexibleserver/servers/list
  (value[].name / version / sku.name / location / highAvailability.mode)
- Service Bus Namespaces/Queues - List →
  https://learn.microsoft.com/en-us/rest/api/servicebus/controlplane-stable/queues/list-by-namespace
- Log Analytics Workspaces - List →
  https://learn.microsoft.com/en-us/rest/api/loganalytics/workspaces/list
  (value[].name / retentionInDays)
"""

from __future__ import annotations

import types
from typing import Any
from unittest import mock

import pytest

from briar.extract._cloud import ComputeResource, DatabaseResource, LogGroup, QueueResource
from briar.extract._clouds import make_cloud
from briar.extract._clouds.azure import AzureCloudProvider

SUB = "00000000-0000-0000-0000-000000000000"

pytestmark = pytest.mark.boundary


@pytest.fixture
def provider() -> AzureCloudProvider:
    prov = AzureCloudProvider(profile=SUB, region="eastus")
    # Pre-seed a fake credential so _build_credential never touches
    # DefaultAzureCredential (which would probe the environment).
    prov._credential = object()
    return prov


def _module_with(**attrs: Any) -> types.SimpleNamespace:
    """A stand-in for an azure-mgmt-* module: a namespace whose
    attributes are the client classes the provider instantiates."""
    return types.SimpleNamespace(**attrs)


def _obj(**kw: Any) -> Any:
    return types.SimpleNamespace(**kw)


# ─── is_available ────────────────────────────────────────────────────


def test_is_available_false_without_subscription() -> None:
    assert AzureCloudProvider().is_available() is False


def test_is_available_true_with_sub_and_identity(mocker: Any) -> None:
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=object())
    assert AzureCloudProvider(profile=SUB).is_available() is True


def test_is_available_false_when_identity_missing(mocker: Any) -> None:
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=None)
    assert AzureCloudProvider(profile=SUB).is_available() is False


# ─── caller_identity ─────────────────────────────────────────────────


def test_caller_identity_returns_subscription(provider: AzureCloudProvider, mocker: Any) -> None:
    client = mock.MagicMock()
    client.subscriptions.get.return_value = _obj(display_name="Acme Prod")
    module = _module_with(SubscriptionClient=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=module)

    identity = provider.caller_identity()

    assert identity.account_id == SUB
    assert identity.region == "eastus"
    assert identity.extra["display_name"] == "Acme Prod"
    client.subscriptions.get.assert_called_once_with(SUB)


def test_caller_identity_raises_when_sdk_missing(provider: AzureCloudProvider, mocker: Any) -> None:
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=None)
    with pytest.raises(RuntimeError) as ctx:
        provider.caller_identity()
    assert "briar-cli[azure]" in str(ctx.value)


# ─── list_compute (Container Apps) ───────────────────────────────────


def test_list_compute_normalises_container_apps(provider: AzureCloudProvider, mocker: Any) -> None:
    client = mock.MagicMock()
    client.container_apps.list_by_subscription.return_value = [
        _obj(name="web", location="eastus"),
        _obj(name="worker", location="westus2"),
    ]
    module = _module_with(ContainerAppsAPIClient=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=module)

    compute = provider.list_compute()

    assert [c.name for c in compute] == ["web", "worker"]
    assert all(isinstance(c, ComputeResource) for c in compute)
    assert compute[0].kind == "container-app"
    assert compute[0].region == "eastus"
    assert compute[1].region == "westus2"


def test_list_compute_returns_empty_when_sdk_missing(provider: AzureCloudProvider, mocker: Any) -> None:
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=None)
    assert provider.list_compute() == []


def test_list_compute_swallows_runtime_error(provider: AzureCloudProvider, mocker: Any) -> None:
    """The @swallow_errors decorator turns an SDK RuntimeError (auth
    failure, throttling) into the [] default — not a raise."""
    client = mock.MagicMock()
    client.container_apps.list_by_subscription.side_effect = RuntimeError("AuthorizationFailed")
    module = _module_with(ContainerAppsAPIClient=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=module)

    assert provider.list_compute() == []


# ─── list_databases (PostgreSQL Flexible Servers) ────────────────────


def test_list_databases_normalises_postgres(provider: AzureCloudProvider, mocker: Any) -> None:
    client = mock.MagicMock()
    client.servers.list.return_value = [
        _obj(
            name="pg-prod",
            version="15",
            sku=_obj(name="Standard_D2s_v3"),
            location="eastus",
            high_availability=_obj(mode="ZoneRedundant"),
        ),
        _obj(
            name="pg-dev",
            version="14",
            sku=None,
            location="eastus",
            high_availability=_obj(mode="Disabled"),
        ),
    ]
    module = _module_with(PostgreSQLManagementClient=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=module)

    dbs = provider.list_databases()

    assert all(isinstance(d, DatabaseResource) for d in dbs)
    by_name = {d.name: d for d in dbs}
    assert by_name["pg-prod"].engine == "postgres"
    assert by_name["pg-prod"].version == "15"
    assert by_name["pg-prod"].instance_class == "Standard_D2s_v3"
    assert by_name["pg-prod"].multi_az is True
    # Disabled HA → multi_az False; sku=None → empty instance_class.
    assert by_name["pg-dev"].multi_az is False
    assert by_name["pg-dev"].instance_class == ""


# ─── list_queues (Service Bus — nested under namespaces) ─────────────


def test_list_queues_flattens_namespaces(provider: AzureCloudProvider, mocker: Any) -> None:
    client = mock.MagicMock()
    ns_id = "/subscriptions/x/resourceGroups/rg-prod/providers/Microsoft.ServiceBus/namespaces/ns1"
    client.namespaces.list.return_value = [_obj(name="ns1", id=ns_id, location="eastus")]
    client.queues.list_by_namespace.return_value = [_obj(name="orders"), _obj(name="events")]
    module = _module_with(ServiceBusManagementClient=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=module)

    queues = provider.list_queues()

    assert all(isinstance(q, QueueResource) for q in queues)
    assert [q.name for q in queues] == ["ns1/orders", "ns1/events"]
    assert queues[0].kind == "service-bus-queue"
    assert queues[0].region == "eastus"
    # The resource group is parsed out of the namespace id.
    client.queues.list_by_namespace.assert_called_once_with(resource_group_name="rg-prod", namespace_name="ns1")


def test_list_queues_skips_namespace_without_resource_group(provider: AzureCloudProvider, mocker: Any) -> None:
    """A namespace whose id has no /resourceGroups/ segment is skipped
    (rg parses empty) — queues.list_by_namespace must never be called."""
    client = mock.MagicMock()
    client.namespaces.list.return_value = [_obj(name="orphan", id="/subscriptions/x/providers/foo", location="eastus")]
    module = _module_with(ServiceBusManagementClient=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=module)

    assert provider.list_queues() == []
    client.queues.list_by_namespace.assert_not_called()


# ─── list_log_groups (Log Analytics workspaces) ──────────────────────


def test_list_log_groups_normalises_workspaces(provider: AzureCloudProvider, mocker: Any) -> None:
    client = mock.MagicMock()
    client.workspaces.list.return_value = [
        _obj(name="law-prod", retention_in_days=90),
        _obj(name="law-dev", retention_in_days=30),
    ]
    module = _module_with(LogAnalyticsManagementClient=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=module)

    groups = provider.list_log_groups()

    assert all(isinstance(g, LogGroup) for g in groups)
    assert [g.name for g in groups] == ["law-prod", "law-dev"]
    assert groups[0].retention_days == 90
    # Azure doesn't expose stored bytes via this API → 0.
    assert groups[0].stored_bytes == 0


def test_list_log_groups_caps_at_top_by_bytes(provider: AzureCloudProvider, mocker: Any) -> None:
    client = mock.MagicMock()
    client.workspaces.list.return_value = [_obj(name=f"law-{i}", retention_in_days=30) for i in range(5)]
    module = _module_with(LogAnalyticsManagementClient=mock.MagicMock(return_value=client))
    mocker.patch("briar.extract._clouds.azure._try_import", return_value=module)

    assert len(provider.list_log_groups(top_by_bytes=2)) == 2


# ─── registry wiring ─────────────────────────────────────────────────


def test_make_cloud_azure_returns_provider() -> None:
    cloud = make_cloud("azure", profile=SUB, region="westeurope")
    assert isinstance(cloud, AzureCloudProvider)
    assert cloud.kind == "azure"
    assert cloud._subscription_id == SUB
    assert cloud._region == "westeurope"
