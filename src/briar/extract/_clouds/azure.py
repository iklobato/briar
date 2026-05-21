"""Azure `CloudProvider`.

Lazy-imports each ``azure-mgmt-*`` library — opt-in via
``pip install briar-cli[azure]``. Auth via ``DefaultAzureCredential``
(picks up Az CLI login, env-var service principal, managed identity,
etc.).

``profile`` carries the subscription ID."""

from __future__ import annotations

import importlib
import logging
from typing import Any, List, Optional

from briar.decorators import swallow_errors
from briar.extract._cloud import (
    AccountIdentity,
    CloudProvider,
    ComputeResource,
    DatabaseResource,
    LogGroup,
    QueueResource,
)


log = logging.getLogger(__name__)


def _try_import(module: str) -> Optional[Any]:
    try:
        return importlib.import_module(module)
    except ImportError:
        return None


class AzureCloudProvider(CloudProvider):
    kind = "azure"

    def __init__(self, *, company: str = "", region: str = "", profile: str = "") -> None:
        self._company = company
        self._region = region or "eastus"
        self._subscription_id = profile
        self._credential = None

    def is_available(self) -> bool:
        if not self._subscription_id:
            return False
        return _try_import("azure.identity") is not None

    def _build_credential(self):
        if self._credential is not None:
            return self._credential
        az_identity = _try_import("azure.identity")
        if az_identity is None:
            raise RuntimeError("azure-identity not installed — run `pip install briar-cli[azure]`")
        self._credential = az_identity.DefaultAzureCredential()
        return self._credential

    def caller_identity(self) -> AccountIdentity:
        sub_module = _try_import("azure.mgmt.subscription")
        if sub_module is None:
            raise RuntimeError("azure-mgmt-subscription not installed — run `pip install briar-cli[azure]`")
        client = sub_module.SubscriptionClient(self._build_credential())
        sub = client.subscriptions.get(self._subscription_id)
        return AccountIdentity(
            account_id=self._subscription_id,
            region=self._region,
            extra={"display_name": getattr(sub, "display_name", "")},
        )

    @swallow_errors(default=[], message="azure list_compute")
    def list_compute(self) -> List[ComputeResource]:
        aca = _try_import("azure.mgmt.appcontainers")
        if aca is None:
            return []
        client = aca.ContainerAppsAPIClient(self._build_credential(), self._subscription_id)
        out: List[ComputeResource] = []
        for app in client.container_apps.list_by_subscription():
            out.append(
                ComputeResource(
                    name=str(getattr(app, "name", "")),
                    kind="container-app",
                    region=str(getattr(app, "location", self._region)),
                )
            )
        return out

    @swallow_errors(default=[], message="azure list_databases")
    def list_databases(self) -> List[DatabaseResource]:
        # Azure Database for PostgreSQL Flexible Servers via azure-mgmt-rdbms.
        rdbms = _try_import("azure.mgmt.rdbms.postgresql_flexibleservers")
        if rdbms is None:
            return []
        client = rdbms.PostgreSQLManagementClient(self._build_credential(), self._subscription_id)
        out: List[DatabaseResource] = []
        for server in client.servers.list():
            sku = getattr(server, "sku", None)
            out.append(
                DatabaseResource(
                    name=str(getattr(server, "name", "")),
                    engine="postgres",
                    version=str(getattr(server, "version", "")),
                    instance_class=str(getattr(sku, "name", "") if sku else ""),
                    region=str(getattr(server, "location", self._region)),
                    multi_az=bool(getattr(getattr(server, "high_availability", None), "mode", "") == "ZoneRedundant"),
                )
            )
        return out

    @swallow_errors(default=[], message="azure list_queues")
    def list_queues(self) -> List[QueueResource]:
        sb = _try_import("azure.mgmt.servicebus")
        if sb is None:
            return []
        client = sb.ServiceBusManagementClient(self._build_credential(), self._subscription_id)
        out: List[QueueResource] = []
        # Service Bus queues are nested under namespaces — flatten them.
        for namespace in client.namespaces.list():
            ns_name = getattr(namespace, "name", "")
            resource_group = (getattr(namespace, "id", "") or "").split("/resourceGroups/")
            rg = resource_group[1].split("/", 1)[0] if len(resource_group) > 1 else ""
            if not rg:
                continue
            for q in client.queues.list_by_namespace(resource_group_name=rg, namespace_name=ns_name):
                out.append(
                    QueueResource(
                        name=f"{ns_name}/{getattr(q, 'name', '')}",
                        kind="service-bus-queue",
                        region=str(getattr(namespace, "location", self._region)),
                    )
                )
        return out

    @swallow_errors(default=[], message="azure list_log_groups")
    def list_log_groups(self, *, top_by_bytes: int = 10) -> List[LogGroup]:
        la = _try_import("azure.mgmt.loganalytics")
        if la is None:
            return []
        client = la.LogAnalyticsManagementClient(self._build_credential(), self._subscription_id)
        out: List[LogGroup] = []
        for ws in client.workspaces.list():
            out.append(
                LogGroup(
                    name=str(getattr(ws, "name", "")),
                    stored_bytes=0,  # Azure doesn't expose bytes directly via this API
                    retention_days=int(getattr(ws, "retention_in_days", 0) or 0),
                )
            )
            if len(out) >= top_by_bytes:
                break
        return out
