"""Azure `CloudProvider`.

Lazy-imports each ``azure-mgmt-*`` library — opt-in via
``pip install briar-cli[azure]``. Auth is the company login (spec 005, I3):
``AzureCliCredential`` for the tenant in `[auth.azure] tenant` (the `az`
login, so `az` must be installed), or ``DefaultAzureCredential`` when it is
unset (az login, env-var service principal, managed identity). The
subscription is `[auth.azure] subscription` (I4)."""

from __future__ import annotations

import importlib
import logging
from functools import partial
from typing import Any, List, Optional, Tuple

from briar.auth.logins import AzureLogin, CompanyLogins
from briar.decorators import swallow_errors
from briar.errors import CliError
from briar.extract._login_failures import AzureFailures
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
    login_source = "azure"
    # The scope `AzureCliCredential` asks a token for in the login check.
    MANAGEMENT_SCOPE = "https://management.azure.com/.default"

    def __init__(self, *, logins: Optional[CompanyLogins] = None, region: str = "") -> None:
        self._logins = logins
        self._region = region or "eastus"
        self._credential = None

    def _login(self) -> AzureLogin:
        return (self._logins or CompanyLogins.defaults("")).azure()

    @property
    def _subscription_id(self) -> str:
        return self._login().subscription()

    def is_available(self) -> bool:
        """A subscription and azure-identity; the login is the job of the check (spec 005, J15)."""
        if not self._subscription_id:
            return False
        return _try_import("azure.identity") is not None

    def scope(self) -> Tuple[str, ...]:
        return (self._subscription_id,) if self._subscription_id else ()

    def who_am_i(self) -> str:
        return AzureFailures().account(self._tenant_token)

    def read(self, resource: str) -> None:
        AzureFailures().read(partial(self._subscription, resource))

    def _tenant_token(self) -> str:
        self._build_credential().get_token(self.MANAGEMENT_SCOPE)
        tenant = self._login().tenant()
        return f"tenant {tenant}" if tenant else "the default tenant"

    def _subscription(self, subscription: str) -> None:
        sub_module = _try_import("azure.mgmt.subscription")
        if sub_module is None:
            raise CliError("azure-mgmt-subscription not installed: run `pip install 'briar-cli[azure]'`")
        sub_module.SubscriptionClient(self._build_credential()).subscriptions.get(subscription)

    def _build_credential(self):
        if self._credential is not None:
            return self._credential
        az_identity = _try_import("azure.identity")
        if az_identity is None:
            raise CliError("azure-identity not installed: run `pip install 'briar-cli[azure]'`")
        tenant = self._login().tenant()
        self._credential = az_identity.AzureCliCredential(tenant_id=tenant) if tenant else az_identity.DefaultAzureCredential()
        return self._credential

    # TODO(timeout-parity): the azure-mgmt-* clients constructed below
    # accept a `transport=` kwarg whose RequestsTransport carries
    # `connection_timeout` / `read_timeout`. AWS got `botocore Config`
    # treatment in Phase 2; this Azure path was deferred because each
    # SDK release exposes the transport differently and the fix needs
    # real-API testing. Until then, every Azure call inherits the
    # azure-core defaults (currently 300s connect / 300s read).

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
