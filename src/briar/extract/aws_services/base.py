"""`AwsServiceGatherer` contract — one class per AWS service.

Each gatherer is responsible for **its own** boto3 calls and the
rendering of one `ExtractedSection`. The orchestrator
(`ExtractAwsInfra`) just walks the registry and concatenates outputs.
This keeps each gatherer at single-responsibility scope and lets new
services (S3, EKS, IAM, …) ship as one file + one entry in the
registry — no edit of the orchestrator required."""

from __future__ import annotations

from typing import Any, ClassVar, Optional

from briar.extract.base import ExtractedSection


class AwsServiceGatherer:
    """Subclasses set `name` + implement `gather(session)`."""

    name: ClassVar[str] = ""

    def gather(self, session: Any) -> Optional[ExtractedSection]:
        raise NotImplementedError
