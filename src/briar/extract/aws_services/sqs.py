"""SQS queues gatherer."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from briar.extract.aws_services.base import AwsServiceGatherer
from briar.extract.base import ExtractedSection


class GatherSqs(AwsServiceGatherer):
    name = "sqs"

    def gather(self, session: Any) -> Optional[ExtractedSection]:
        sqs = session.client("sqs")
        urls = sqs.list_queues().get("QueueUrls", [])
        if not urls:
            return ExtractedSection(title="SQS", body="_no queues_")
        rows: List[Dict[str, Any]] = [
            {"url": u, "name": u.rsplit("/", 1)[-1]} for u in urls
        ]
        lines = [f"- {r['name']}" for r in rows]
        return ExtractedSection(
            title=f"SQS ({len(rows)} queue(s))",
            body="\n".join(lines),
            data={"queues": rows},
        )
