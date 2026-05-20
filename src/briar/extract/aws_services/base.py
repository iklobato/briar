"""`AwsServiceGatherer` contract — one class per AWS service.

Each gatherer is responsible for **its own** boto3 calls and the
rendering of one `ExtractedSection`. The orchestrator
(`ExtractAwsInfra`) just walks the registry and concatenates outputs.
This keeps each gatherer at single-responsibility scope and lets new
services (S3, EKS, IAM, …) ship as one file + one entry in the
registry — no edit of the orchestrator required."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Optional

from briar.extract.base import ExtractedSection


class AwsServiceGatherer(ABC):
    """Subclasses set `name` + implement `gather(session)`."""

    name: ClassVar[str] = ""

    @abstractmethod
    def gather(self, session: Any) -> Optional[ExtractedSection]:
        """Call this service's APIs through `session` and emit a section."""
