"""`AwsServiceGatherer` contract — one class per AWS service.

Each gatherer is responsible for **its own** boto3 calls and the
rendering of one `ExtractedSection`. The orchestrator
(`ExtractAwsInfra`) just walks the registry and concatenates outputs.
This keeps each gatherer at single-responsibility scope and lets new
services (S3, EKS, IAM, …) ship as one file + one entry in the
registry — no edit of the orchestrator required."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from briar.extract.base import ExtractedSection


class AwsServiceGatherer(ABC):
    """Subclasses set `name` + `data_key` + implement `gather(session)`.

    `data_key` names the field inside `ExtractedSection.data` that
    holds the per-row list this gatherer produces (ECS: `services`,
    Lambda: `functions`, etc.). Lets `AwsCloudProvider._gather_via`
    locate the rows without an open-ended string dispatch like
    ``for key in ("services", "functions", "queues", "items"):``."""

    name: ClassVar[str] = ""
    data_key: ClassVar[str] = ""

    @abstractmethod
    def gather(self, session: Any) -> ExtractedSection:
        """Call this service's APIs through `session` and emit a section."""
