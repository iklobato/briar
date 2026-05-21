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
