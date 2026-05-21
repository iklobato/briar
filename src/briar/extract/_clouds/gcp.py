"""GCP `CloudProvider` — stub.

Implement via ``google-cloud-*`` per-service libraries:
- ``google-cloud-run`` for compute (Cloud Run services + jobs)
- ``google-cloud-sql`` for databases
- ``google-cloud-pubsub`` for queues (topics + subscriptions)
- ``google-cloud-logging`` for log buckets / sinks

Auth: ambient ADC via ``GOOGLE_APPLICATION_CREDENTIALS`` env var
pointing at a service-account JSON, or ``gcloud auth application-default
login`` for local dev. Pattern matches AWS's ambient credential chain."""

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


class GcpCloudProvider(CloudProvider):
    kind = "gcp"

    def __init__(self, *, company: str = "", region: str = "", profile: str = "") -> None:
        self._company = company
        self._region = region or "us-central1"
        # GCP equivalent of "profile" is the project ID. `profile` arg
        # carries it for symmetry with the AWS adapter signature.
        self._project_id = profile

    def is_available(self) -> bool:
        # Conservative: only available when the operator opts in by
        # setting a project id. The Google ADC chain handles the actual
        # credential lookup at call time.
        return bool(self._project_id)

    def caller_identity(self) -> AccountIdentity:
        raise NotImplementedError(
            "GcpCloudProvider.caller_identity is not implemented yet. Use "
            "google.auth.default() to grab the credentials, then call "
            "credentials.service_account_email or fall back to "
            "client.list_organizations() for org-level identity. Return "
            "AccountIdentity(account_id=project_id, region=self._region)."
        )

    def list_compute(self) -> List[ComputeResource]:
        raise NotImplementedError(
            "GcpCloudProvider.list_compute — Cloud Run: "
            "google.cloud.run_v2.ServicesClient().list_services("
            "parent=f'projects/{project}/locations/{region}'). Translate each "
            "Service onto ComputeResource(name=svc.name, kind='cloud-run', region)."
        )

    def list_databases(self) -> List[DatabaseResource]:
        raise NotImplementedError(
            "GcpCloudProvider.list_databases — Cloud SQL Admin API: "
            "googleapiclient.discovery.build('sqladmin','v1').instances().list("
            "project=project). Translate each instance onto DatabaseResource."
        )

    def list_queues(self) -> List[QueueResource]:
        raise NotImplementedError(
            "GcpCloudProvider.list_queues — Pub/Sub: "
            "google.cloud.pubsub_v1.PublisherClient().list_topics("
            "request={'project': f'projects/{project}'}). One QueueResource per topic; "
            "kind='pubsub-topic'."
        )

    def list_log_groups(self, *, top_by_bytes: int = 10) -> List[LogGroup]:
        raise NotImplementedError(
            "GcpCloudProvider.list_log_groups — Cloud Logging: "
            "google.cloud.logging_v2.Client().list_buckets(parent=f'projects/{project}/locations/-'). "
            "GCP doesn't expose `stored_bytes` per bucket via the public API; you may "
            "have to query the Monitoring API for `logging.googleapis.com/billing/bytes_ingested`."
        )
