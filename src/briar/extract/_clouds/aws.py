"""AWS `CloudProvider`. Wraps the existing `aws_services/` gatherers.

The internal `AWS_SERVICE_GATHERERS` registry (ecs, rds, lambda, sqs,
logs) stays as-is — it's already a clean Strategy + Registry. This
adapter just translates its `ExtractedSection` outputs into the
`CloudProvider` dataclasses so the outer extractor doesn't need to
know AWS-specific section layouts."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.extract._cloud import (
    AccountIdentity,
    CloudProvider,
    ComputeResource,
    DatabaseResource,
    LogGroup,
    QueueResource,
)


log = logging.getLogger(__name__)


class AwsCloudProvider(CloudProvider):
    kind = "aws"

    def __init__(self, *, company: str = "", region: str = "", profile: str = "") -> None:
        self._company = company
        self._region = region or "us-east-1"
        # `profile` is the explicit local-profile name; falls back to
        # per-company env vars (AWS_<COMPANY>_*) — same logic the
        # legacy _BotoSessionBuilder used.
        self._profile = profile or company
        self._session: Any = None

    def _make_session(self):
        if self._session is not None:
            return self._session
        import boto3

        key_id = CredEnv.AWS_KEY_ID.read(self._profile) if self._profile else ""
        secret = CredEnv.AWS_SECRET.read(self._profile) if self._profile else ""
        if key_id and secret:
            self._session = boto3.Session(
                aws_access_key_id=key_id,
                aws_secret_access_key=secret,
                aws_session_token=CredEnv.AWS_SESSION.read(self._profile),
                region_name=self._region,
            )
        else:
            self._session = boto3.Session(profile_name=self._profile or None, region_name=self._region)
        return self._session

    def is_available(self) -> bool:
        try:
            import boto3  # noqa: F401

            return True
        except ImportError:
            return False

    def caller_identity(self) -> AccountIdentity:
        session = self._make_session()
        identity = session.client("sts").get_caller_identity()
        return AccountIdentity(account_id=str(identity.get("Account", "?")), region=self._region)

    @swallow_errors(default=[], message="aws list_compute")
    def list_compute(self) -> List[ComputeResource]:
        out: List[ComputeResource] = []
        out.extend(self._gather_via("ecs", kind="ecs-service"))
        out.extend(self._gather_via("lambda", kind="lambda"))
        return out

    @swallow_errors(default=[], message="aws list_databases")
    def list_databases(self) -> List[DatabaseResource]:
        from briar.extract.aws_services import AWS_SERVICE_GATHERERS

        gatherer = AWS_SERVICE_GATHERERS.get("rds")
        if not gatherer:
            return []
        section = gatherer.gather(self._make_session())
        rows = (section.data or {}).get("instances", []) if section.data else []
        out: List[DatabaseResource] = []
        for row in rows:
            out.append(
                DatabaseResource(
                    name=str(row.get("identifier") or ""),
                    engine=str(row.get("engine") or ""),
                    version=str(row.get("version") or ""),
                    instance_class=str(row.get("class") or ""),
                    region=self._region,
                    multi_az=bool(row.get("multi_az")),
                    extra={"allocated_gb": row.get("allocated_gb")},
                )
            )
        return out

    @swallow_errors(default=[], message="aws list_queues")
    def list_queues(self) -> List[QueueResource]:
        return self._gather_via("sqs", kind="sqs", as_queue=True)

    @swallow_errors(default=[], message="aws list_log_groups")
    def list_log_groups(self, *, top_by_bytes: int = 10) -> List[LogGroup]:
        from briar.extract.aws_services import AWS_SERVICE_GATHERERS

        gatherer = AWS_SERVICE_GATHERERS.get("logs")
        if not gatherer:
            return []
        section = gatherer.gather(self._make_session())
        rows = (section.data or {}).get("groups", []) if section.data else []
        out: List[LogGroup] = []
        for row in rows[:top_by_bytes]:
            out.append(
                LogGroup(
                    name=str(row.get("name") or ""),
                    stored_bytes=int(row.get("stored_bytes") or 0),
                    retention_days=int(row.get("retention_days") or 0),
                )
            )
        return out

    def list_subsections(self, *, services: List[str] = None) -> List[Any]:  # type: ignore[assignment]
        """AWS native renderer — preserves the original `aws-infra`
        markdown shape by delegating to the per-service gatherer
        registry (ECS / RDS / Lambda / SQS / Logs). Each gatherer
        emits its own ExtractedSection with its own per-service
        body format; the result reads richer than the generic
        Compute/Databases/Queues walker the base class provides.

        Optional ``services`` filter: subset of
        ``AWS_SERVICE_GATHERERS.keys()`` to gather. Empty/None means
        all of them."""
        from briar.extract.aws_services import AWS_SERVICE_GATHERERS
        from briar.extract.base import ExtractedSection

        selected = services or list(AWS_SERVICE_GATHERERS.keys())
        out: List[Any] = []
        session = self._make_session()
        for svc_name in selected:
            gatherer = AWS_SERVICE_GATHERERS.get(svc_name)
            if gatherer is None:
                continue
            try:
                section = gatherer.gather(session)
            except Exception as exc:  # noqa: BLE001
                section = ExtractedSection(title=svc_name.upper(), body=f"_skipped — {exc}_")
            if not section.is_empty:
                out.append(section)
        return out

    def _gather_via(self, svc: str, *, kind: str, as_queue: bool = False) -> List[Any]:
        from briar.extract.aws_services import AWS_SERVICE_GATHERERS

        gatherer = AWS_SERVICE_GATHERERS.get(svc)
        if not gatherer:
            return []
        section = gatherer.gather(self._make_session())
        data = section.data or {}
        rows: List[Any] = []
        # Per-service shape lives in aws_services/<svc>.py. ECS returns
        # 'services'; Lambda returns 'functions'; SQS returns 'queues'.
        for key in ("services", "functions", "queues", "items"):
            if key in data and isinstance(data[key], list):
                rows = data[key]
                break
        out: List[Any] = []
        for row in rows:
            name = str(row.get("name") or row.get("identifier") or "")
            if as_queue:
                out.append(QueueResource(name=name, kind=kind, region=self._region, extra=row))
            else:
                out.append(ComputeResource(name=name, kind=kind, region=self._region, extra=row))
        return out
