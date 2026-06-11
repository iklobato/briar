"""Account-wide resource inventory via the Resource Groups Tagging API.

Where the other gatherers describe ONE service each (ECS, RDS, …), this
one walks `resourcegroupstaggingapi:GetResources` — a single paginated
API that enumerates every *tagged* resource across every service in the
region. It's the breadth complement to the per-service depth gatherers.

Two deliberate shapes:
- ``body`` stays terse: a per-service COUNT only. The body is what gets
  rendered into the prompt-baked markdown blob, so it must stay small
  regardless of account size.
- ``data["resources"]`` carries the FULL per-resource detail (ARN,
  service, type, region, tags). That rides in the structured payload the
  composer can persist as a JSON inventory companion — never in the
  prompt.

Limitation worth stating: GetResources only returns resources that have
at least one tag. Untagged resources are invisible to it. For a truly
exhaustive map (incl. untagged) you'd add an AWS Config / Resource
Explorer gatherer alongside this one."""

from __future__ import annotations

from typing import Any, Dict, List

from briar.extract.aws_services.base import AwsServiceGatherer
from briar.extract.base import ExtractedSection

_PER_PAGE = 100  # GetResources hard max is 100 ResourcesPerPage.


def _parse_arn(arn: str) -> Dict[str, str]:
    """Split an ARN into the fields we surface. ARNs come in three
    resource shapes — ``type/id``, ``type:id``, and bare ``id`` — which
    we normalise to ``type`` + ``name``.

        arn:aws:sqs:us-east-1:111:orders           → ("", "orders")
        arn:aws:rds:us-east-1:111:db:primary       → ("db", "primary")
        arn:aws:s3:::my-bucket                      → ("", "my-bucket")
    """
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return {
            "service": parts[2] if len(parts) > 2 else "",
            "region": parts[3] if len(parts) > 3 else "",
            "type": "",
            "name": arn,
        }
    service, region, resource = parts[2], parts[3], parts[5]
    if "/" in resource:
        rtype, name = resource.split("/", 1)
    elif ":" in resource:
        rtype, name = resource.split(":", 1)
    else:
        rtype, name = "", resource
    return {"service": service, "region": region, "type": rtype, "name": name}


class GatherTaggingInventory(AwsServiceGatherer):
    name = "tagging-inventory"
    data_key = "resources"

    def gather(self, session: Any) -> ExtractedSection:
        client = session.client("resourcegroupstaggingapi")
        paginator = client.get_paginator("get_resources")
        rows: List[Dict[str, Any]] = []
        for page in paginator.paginate(ResourcesPerPage=_PER_PAGE):
            for mapping in page.get("ResourceTagMappingList", []):
                arn = mapping.get("ResourceARN", "")
                tags = {t["Key"]: t.get("Value", "") for t in mapping.get("Tags", [])}
                rows.append({"arn": arn, **_parse_arn(arn), "tags": tags})

        if not rows:
            return ExtractedSection(title="Resource inventory", body="_no tagged resources_")

        counts: Dict[str, int] = {}
        for row in rows:
            counts[row["service"]] = counts.get(row["service"], 0) + 1
        # body = per-service counts only (terse, prompt-safe); the full
        # resource list lives in data, not here.
        lines = [f"- {svc}: {counts[svc]}" for svc in sorted(counts)]
        return ExtractedSection(
            title=f"Resource inventory ({len(rows)} tagged resource(s), {len(counts)} service(s))",
            body="\n".join(lines),
            data={"resources": rows},
        )
