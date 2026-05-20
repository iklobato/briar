"""Lambda functions gatherer.

Module suffix `_` avoids shadowing the `lambda` keyword."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from briar.extract.aws_services.base import AwsServiceGatherer
from briar.extract.base import ExtractedSection


class GatherLambda(AwsServiceGatherer):
    name = "lambda"

    def gather(self, session: Any) -> Optional[ExtractedSection]:
        lam = session.client("lambda")
        paginator = lam.get_paginator("list_functions")
        functions: List[Dict[str, Any]] = []
        for page in paginator.paginate():
            for f in page.get("Functions", []):
                functions.append({
                    "name": f.get("FunctionName"),
                    "runtime": f.get("Runtime"),
                    "memory_mb": f.get("MemorySize"),
                    "timeout_s": f.get("Timeout"),
                    "last_modified": f.get("LastModified"),
                })
        if not functions:
            return ExtractedSection(title="Lambda", body="_no functions_")
        lines = [
            f"- {f['name']}  {f['runtime']}  mem={f['memory_mb']}MB  "
            f"timeout={f['timeout_s']}s"
            for f in functions
        ]
        return ExtractedSection(
            title=f"Lambda ({len(functions)} function(s))",
            body="\n".join(lines),
            data={"functions": functions},
        )
