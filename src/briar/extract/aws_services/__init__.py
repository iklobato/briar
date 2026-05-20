"""AWS service gatherer registry — Strategy + Factory.

Adding a new service (S3 / EKS / IAM / ...) = one file in this
directory + one entry below. `ExtractAwsInfra` walks the registry; no
edits to the orchestrator are needed."""

from __future__ import annotations

from typing import Dict

from briar.extract.aws_services.base import AwsServiceGatherer
from briar.extract.aws_services.ecs import GatherEcs
from briar.extract.aws_services.lambda_ import GatherLambda
from briar.extract.aws_services.logs import GatherLogs
from briar.extract.aws_services.rds import GatherRds
from briar.extract.aws_services.sqs import GatherSqs


AWS_SERVICE_GATHERERS: Dict[str, AwsServiceGatherer] = {
    g.name: g for g in (
        GatherEcs(),
        GatherRds(),
        GatherLambda(),
        GatherSqs(),
        GatherLogs(),
    )
}


__all__ = ["AwsServiceGatherer", "AWS_SERVICE_GATHERERS"]
