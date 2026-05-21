"""Azure `CloudProvider` — stub.

Implement via ``azure-mgmt-*`` per-service libraries with
``azure.identity.DefaultAzureCredential`` for ambient auth (same
pattern as boto3/ADC)."""

from __future__ import annotations

from typing import List

from briar.extract._cloud import (
    AccountIdentity,
    CloudProvider,
    ComputeResource,
    DatabaseResource,
    LogGroup,
    QueueResource,
)


class AzureCloudProvider(CloudProvider):
    kind = "azure"

    def __init__(self, *, company: str = "", region: str = "", profile: str = "") -> None:
        self._company = company
        self._region = region or "eastus"
        # `profile` carries the subscription ID for symmetry with AWS.
        self._subscription_id = profile

    def is_available(self) -> bool:
        return bool(self._subscription_id)

    def caller_identity(self) -> AccountIdentity:
        raise NotImplementedError(
            "AzureCloudProvider.caller_identity — use azure.identity.DefaultAzureCredential() + "
            "azure.mgmt.subscription.SubscriptionClient(cred).subscriptions.get(self._subscription_id). "
            "Return AccountIdentity(account_id=self._subscription_id, region=self._region)."
        )

    def list_compute(self) -> List[ComputeResource]:
        raise NotImplementedError(
            "AzureCloudProvider.list_compute — Container Apps: "
            "azure.mgmt.appcontainers.ContainerAppsAPIClient(cred, sub).container_apps.list_by_subscription(). "
            "Map ContainerApp objects onto ComputeResource(kind='aci')."
        )

    def list_databases(self) -> List[DatabaseResource]:
        raise NotImplementedError(
            "AzureCloudProvider.list_databases — Azure Database for PostgreSQL/MySQL: "
            "azure.mgmt.rdbms.postgresql_flexibleservers.PostgreSQLManagementClient(cred,sub).servers.list()."
        )

    def list_queues(self) -> List[QueueResource]:
        raise NotImplementedError(
            "AzureCloudProvider.list_queues — Service Bus: "
            "azure.mgmt.servicebus.ServiceBusManagementClient(cred,sub).queues.list_by_namespace()."
        )

    def list_log_groups(self, *, top_by_bytes: int = 10) -> List[LogGroup]:
        raise NotImplementedError(
            "AzureCloudProvider.list_log_groups — Log Analytics workspaces: "
            "azure.mgmt.loganalytics.LogAnalyticsManagementClient(cred,sub).workspaces.list()."
        )
