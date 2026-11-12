"""GCP `CloudProvider`.

Lazy-imports each Google library — opt-in via
``pip install briar-cli[gcp]``. Auth is the company login (spec 005, G26,
I2): the ADC file of the gcloud config folder in `[auth.gcp] config_dir`,
or the Application Default Credentials chain when it is unset. The project
is `[auth.gcp] project` (I4)."""

from __future__ import annotations

import importlib
import logging
from functools import partial
from typing import Any, ClassVar, List, Optional, Tuple

from briar.auth.logins import CompanyLogins, GcpLogin
from briar.decorators import swallow_errors
from briar.errors import CliError
from briar.extract._login_failures import GoogleFailures
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


class GcpCloudProvider(CloudProvider):
    kind = "gcp"
    login_source = "gcp"

    SCOPES: ClassVar[Tuple[str, ...]] = ("https://www.googleapis.com/auth/cloud-platform",)

    def __init__(self, *, logins: Optional[CompanyLogins] = None, region: str = "") -> None:
        self._logins = logins
        self._region = region or "us-central1"

    def _login(self) -> GcpLogin:
        return (self._logins or CompanyLogins.defaults("")).gcp()

    @property
    def _project_id(self) -> str:
        return self._login().project()

    def is_available(self) -> bool:
        """A project and google-auth; the login is the job of the check (spec 005, J15)."""
        if not self._project_id:
            return False
        return _try_import("google.auth") is not None

    def scope(self) -> Tuple[str, ...]:
        return (self._project_id,) if self._project_id else ()

    def who_am_i(self) -> str:
        return GoogleFailures().account(self._refreshed_account)

    def read(self, resource: str) -> None:
        GoogleFailures().read(partial(self._project, resource))

    def _refreshed_account(self) -> str:
        credentials = self._credentials()
        transport = importlib.import_module("google.auth.transport.requests")
        credentials.refresh(transport.Request())
        return str(getattr(credentials, "service_account_email", "") or getattr(credentials, "account", "") or "application default credentials")

    def _project(self, project: str) -> None:
        discovery = _try_import("googleapiclient.discovery")
        if discovery is None:
            raise CliError("google-api-python-client not installed: run `pip install 'briar-cli[gcp]'`")
        manager = discovery.build("cloudresourcemanager", "v1", credentials=self._credentials(), cache_discovery=False)
        manager.projects().get(projectId=project).execute()

    def _credentials(self) -> Any:
        """The ADC of the company config folder, or the google-auth default chain."""
        google_auth = _try_import("google.auth")
        if google_auth is None:
            raise CliError("google-auth not installed: run `pip install 'briar-cli[gcp]'`")
        adc_file = self._login().adc_file()
        if adc_file is None:
            return google_auth.default(scopes=list(self.SCOPES))[0]
        return google_auth.load_credentials_from_file(str(adc_file), scopes=list(self.SCOPES))[0]

    # TODO(timeout-parity): every google.cloud.* client below accepts
    # a `client_options` or `transport` kwarg whose timeout knob varies
    # per SDK (`google.api_core.client_options.ClientOptions` for v2
    # libs; `httplib2.Http(timeout=...)` for googleapiclient.discovery).
    # AWS got `botocore Config` treatment in Phase 2; this GCP path
    # was deferred because each SDK release exposes timeouts
    # differently and the fix needs real-API testing. Until then,
    # every GCP call inherits the SDK defaults (typically 60s).

    def caller_identity(self) -> AccountIdentity:
        # GCP doesn't have a single "caller identity" endpoint analogous
        # to STS; the project_id IS the identity. Just verify ADC works.
        self._credentials()  # raises on a missing ADC
        return AccountIdentity(account_id=self._project_id, region=self._region)

    @swallow_errors(default=[], message="gcp list_compute")
    def list_compute(self) -> List[ComputeResource]:
        run_v2 = _try_import("google.cloud.run_v2")
        if run_v2 is None:
            return []
        client = run_v2.ServicesClient(credentials=self._credentials())
        parent = f"projects/{self._project_id}/locations/{self._region}"
        out: List[ComputeResource] = []
        for service in client.list_services(parent=parent):
            name = service.name.rsplit("/", 1)[-1] if service.name else ""
            out.append(ComputeResource(name=name, kind="cloud-run", region=self._region))
        return out

    @swallow_errors(default=[], message="gcp list_databases")
    def list_databases(self) -> List[DatabaseResource]:
        googleapi = _try_import("googleapiclient.discovery")
        if googleapi is None:
            return []
        service = googleapi.build("sqladmin", "v1", credentials=self._credentials(), cache_discovery=False)
        out: List[DatabaseResource] = []
        request = service.instances().list(project=self._project_id)
        while request is not None:
            resp = request.execute()
            for inst in resp.get("items", []) or []:
                settings = inst.get("settings") or {}
                out.append(
                    DatabaseResource(
                        name=str(inst.get("name") or ""),
                        engine=str(inst.get("databaseVersion") or "").split("_")[0].lower(),
                        version=str(inst.get("databaseVersion") or ""),
                        instance_class=str(settings.get("tier") or ""),
                        region=str(inst.get("region") or self._region),
                        multi_az=bool(settings.get("availabilityType") == "REGIONAL"),
                    )
                )
            request = service.instances().list_next(previous_request=request, previous_response=resp)
        return out

    @swallow_errors(default=[], message="gcp list_queues")
    def list_queues(self) -> List[QueueResource]:
        pubsub_v1 = _try_import("google.cloud.pubsub_v1")
        if pubsub_v1 is None:
            return []
        publisher = pubsub_v1.PublisherClient(credentials=self._credentials())
        out: List[QueueResource] = []
        for topic in publisher.list_topics(request={"project": f"projects/{self._project_id}"}):
            name = topic.name.rsplit("/", 1)[-1] if topic.name else ""
            out.append(QueueResource(name=name, kind="pubsub-topic", region=self._region))
        return out

    @swallow_errors(default=[], message="gcp list_log_groups")
    def list_log_groups(self, *, top_by_bytes: int = 10) -> List[LogGroup]:
        logging_v2 = _try_import("google.cloud.logging_v2")
        if logging_v2 is None:
            return []
        client = logging_v2.Client(project=self._project_id, credentials=self._credentials())
        buckets_client = client.list_buckets(parent=f"projects/{self._project_id}/locations/-")
        out: List[LogGroup] = []
        for bucket in buckets_client:
            # GCP doesn't expose per-bucket `stored_bytes` via this API.
            # That data requires the Cloud Monitoring API
            # (logging.googleapis.com/billing/bytes_ingested) — left as
            # future enhancement.
            out.append(
                LogGroup(
                    name=str(getattr(bucket, "name", "")),
                    stored_bytes=0,
                    retention_days=int(getattr(bucket, "retention_days", 0) or 0),
                )
            )
            if len(out) >= top_by_bytes:
                break
        return out
