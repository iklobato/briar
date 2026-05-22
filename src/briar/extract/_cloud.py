"""`CloudProvider` — vendor-neutral facade for cloud-infra extraction.

Symmetric to `RepositoryProvider` and `TrackerProvider`. Where the
existing `aws_services/` directory is *Strategy + Registry inside one
vendor* (5 AWS services), `CloudProvider` is *Strategy across vendors*.
The outer extractor (`cloud-infra`) is provider-agnostic; each
concrete provider re-uses its own internal gatherer registry.

The dataclass shapes here are deliberately coarse — every cloud has
a "compute" concept, a "database" concept, a "queue" concept — even
if the underlying primitives differ wildly (ECS Task vs Cloud Run
Service vs Azure Container Instance). The provider does the
normalisation; the extractor renders one shape."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComputeResource:
    """One running workload (ECS service, Cloud Run service, Azure
    Container App, k8s deployment, …). Coarse on purpose."""

    name: str
    kind: str  # 'ecs-service', 'lambda', 'cloud-run', 'aci', ...
    region: str
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatabaseResource:
    name: str
    engine: str  # 'postgres', 'mysql', 'mongo', ...
    version: str
    instance_class: str
    region: str
    multi_az: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QueueResource:
    name: str
    kind: str  # 'sqs-standard', 'sqs-fifo', 'pubsub-topic', ...
    region: str
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LogGroup:
    name: str
    stored_bytes: int
    retention_days: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AccountIdentity:
    """`sts:GetCallerIdentity` analogue — every cloud has one."""

    account_id: str
    region: str
    extra: Dict[str, Any] = field(default_factory=dict)


class CloudProvider(ABC):
    """Strategy contract."""

    kind: ClassVar[str] = ""

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def caller_identity(self) -> AccountIdentity:
        """Return account ID + region. Equivalent to `sts:GetCallerIdentity`
        on AWS, `gcloud auth list` on GCP, `az account show` on Azure."""

    def list_compute(self) -> List[ComputeResource]:
        return []

    def list_databases(self) -> List[DatabaseResource]:
        return []

    def list_queues(self) -> List[QueueResource]:
        return []

    def list_log_groups(self, *, top_by_bytes: int = 10) -> List[LogGroup]:
        return []

    def list_subsections(self, *, services: List[str] = None) -> List[Any]:  # type: ignore[assignment]
        """Provider-specific subsection rendering. Default impl walks
        the four list_* verbs above and builds generic Compute /
        Databases / Queues / Log-groups subsections. Subclasses with
        a richer native model (AWS has the `aws_services/` per-service
        gatherers) override this to keep their markdown shape stable
        without forcing the extractor to special-case the cloud kind.

        Return type is List[ExtractedSection] but typed as List[Any]
        here to avoid an import cycle (`extract.base -> _cloud -> base`).
        ``services`` is an optional whitelist filter — only AWS uses
        it today; other clouds ignore it."""
        from briar.extract.base import ExtractedSection

        out: List[ExtractedSection] = []
        compute = self.list_compute()
        if compute:
            out.append(
                ExtractedSection(
                    title="Compute",
                    body="\n".join(f"- {c.name} ({c.kind}, {c.region})" for c in compute),
                    data={"resources": [{"name": c.name, "kind": c.kind, "region": c.region} for c in compute]},
                )
            )
        dbs = self.list_databases()
        if dbs:
            out.append(
                ExtractedSection(
                    title="Databases",
                    body="\n".join(f"- {d.name} {d.engine} {d.version} ({d.instance_class})" for d in dbs),
                    data={"instances": [{"identifier": d.name, "engine": d.engine, "version": d.version, "class": d.instance_class, "multi_az": d.multi_az} for d in dbs]},
                )
            )
        queues = self.list_queues()
        if queues:
            out.append(
                ExtractedSection(
                    title="Queues",
                    body="\n".join(f"- {q.name} ({q.kind})" for q in queues),
                    data={"queues": [{"name": q.name, "kind": q.kind} for q in queues]},
                )
            )
        logs = self.list_log_groups(top_by_bytes=10)
        if logs:
            out.append(
                ExtractedSection(
                    title="Log groups (top 10 by size)",
                    body="\n".join(f"- {g.name} ({g.stored_bytes} bytes, retention={g.retention_days}d)" for g in logs),
                    data={"groups": [{"name": g.name, "stored_bytes": g.stored_bytes, "retention_days": g.retention_days} for g in logs]},
                )
            )
        return out
