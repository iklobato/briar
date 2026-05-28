"""CloudWatch log groups gatherer (top 10 by stored size)."""

from __future__ import annotations

from typing import Any, Dict, List

from briar.extract.aws_services.base import AwsServiceGatherer
from briar.extract.base import ExtractedSection


_TOP_N = 10


class GatherLogs(AwsServiceGatherer):
    name = "logs"
    data_key = "top_log_groups"

    def gather(self, session: Any) -> ExtractedSection:
        logs = session.client("logs")
        paginator = logs.get_paginator("describe_log_groups")
        groups: List[Dict[str, Any]] = []
        for page in paginator.paginate():
            for g in page.get("logGroups", []):
                groups.append(
                    {
                        "name": g.get("logGroupName"),
                        "stored_bytes": g.get("storedBytes", 0),
                        "retention_days": g.get("retentionInDays"),
                    }
                )
        if not groups:
            return ExtractedSection(title="CloudWatch Logs", body="_no groups_")
        groups.sort(key=lambda g: g["stored_bytes"], reverse=True)
        top = groups[:_TOP_N]
        lines = [f"- {g['name']}  {g['stored_bytes'] // (1024 * 1024)}MB  " f"retention={g['retention_days']}d" for g in top]
        return ExtractedSection(
            title=f"CloudWatch Logs (top {len(top)} by size, of {len(groups)})",
            body="\n".join(lines),
            data={"top_log_groups": top},
        )
