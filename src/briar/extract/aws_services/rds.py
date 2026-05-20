"""RDS DB instances gatherer."""

from __future__ import annotations

from typing import Any, Dict, List

from briar.extract.aws_services.base import AwsServiceGatherer
from briar.extract.base import ExtractedSection


class GatherRds(AwsServiceGatherer):
    name = "rds"

    def gather(self, session: Any) -> ExtractedSection:
        rds = session.client("rds")
        instances = rds.describe_db_instances().get("DBInstances", [])
        if not instances:
            return ExtractedSection(title="RDS", body="_no instances_")
        rows: List[Dict[str, Any]] = []
        for db in instances:
            rows.append(
                {
                    "id": db.get("DBInstanceIdentifier"),
                    "engine": f"{db.get('Engine')} {db.get('EngineVersion', '')}",
                    "class": db.get("DBInstanceClass"),
                    "storage_gb": db.get("AllocatedStorage"),
                    "multi_az": db.get("MultiAZ"),
                }
            )
        lines = [f"- {r['id']}  {r['engine']}  {r['class']}  {r['storage_gb']}GB" + ("  Multi-AZ" if r["multi_az"] else "") for r in rows]
        return ExtractedSection(
            title=f"RDS ({len(rows)} instance(s))",
            body="\n".join(lines),
            data={"instances": rows},
        )
