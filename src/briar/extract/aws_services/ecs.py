"""ECS clusters + services gatherer."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from briar.extract.aws_services.base import AwsServiceGatherer
from briar.extract.base import ExtractedSection


class GatherEcs(AwsServiceGatherer):
    name = "ecs"

    def gather(self, session: Any) -> Optional[ExtractedSection]:
        ecs = session.client("ecs")
        clusters = ecs.list_clusters().get("clusterArns", [])
        services: List[Dict[str, Any]] = []
        for cluster_arn in clusters:
            arns = ecs.list_services(cluster=cluster_arn).get("serviceArns", [])
            if not arns:
                continue
            described = ecs.describe_services(
                cluster=cluster_arn, services=arns,
            ).get("services", [])
            for s in described:
                services.append({
                    "cluster": cluster_arn.rsplit("/", 1)[-1],
                    "name": s.get("serviceName"),
                    "desired": s.get("desiredCount"),
                    "running": s.get("runningCount"),
                    "task_def": s.get("taskDefinition", "").rsplit("/", 1)[-1],
                })
        if not services:
            return ExtractedSection(title="ECS", body="_no services_")
        lines = [
            f"- {s['cluster']}/{s['name']}  task={s['task_def']}  "
            f"running={s['running']}/{s['desired']}"
            for s in services
        ]
        return ExtractedSection(
            title=f"ECS ({len(services)} service(s))",
            body="\n".join(lines),
            data={"services": services},
        )
