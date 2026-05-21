"""GCP `CloudProvider`.

Lazy-imports each Google library — opt-in via
``pip install briar-cli[gcp]``. Auth uses Application Default
Credentials (``GOOGLE_APPLICATION_CREDENTIALS`` pointing at a
service-account JSON, or ``gcloud auth application-default login``
locally).

``profile`` carries the GCP project ID for symmetry with the
AwsCloudProvider signature (where ``profile`` is the AWS profile
name)."""

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


class GcpCloudProvider(CloudProvider):
    kind = "gcp"

    def __init__(self, *, company: str = "", region: str = "", profile: str = "") -> None:
        self._company = company
        self._region = region or "us-central1"
        self._project_id = profile

    def is_available(self) -> bool:
        # Conservative: needs both a project id AND at least the auth library.
        if not self._project_id:
            return False
        return _try_import("google.auth") is not None

    def caller_identity(self) -> AccountIdentity:
        # GCP doesn't have a single "caller identity" endpoint analogous
        # to STS; the project_id IS the identity. Just verify ADC works.
        google_auth = _try_import("google.auth")
        if google_auth is None:
            raise RuntimeError("google-auth not installed — run `pip install briar-cli[gcp]`")
        google_auth.default()  # raises on missing ADC
        return AccountIdentity(account_id=self._project_id, region=self._region)

    @swallow_errors(default=[], message="gcp list_compute")
    def list_compute(self) -> List[ComputeResource]:
        run_v2 = _try_import("google.cloud.run_v2")
        if run_v2 is None:
            return []
        client = run_v2.ServicesClient()
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
        service = googleapi.build("sqladmin", "v1", cache_discovery=False)
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
        publisher = pubsub_v1.PublisherClient()
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
        client = logging_v2.Client(project=self._project_id)
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
