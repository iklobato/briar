"""JSON formatter."""

from __future__ import annotations

import json
from typing import Any, List, Optional


class FormatJson:
    name = "json"

    def render(
        self,
        payload: Any,
        columns: Optional[List[str]] = None,
    ) -> None:
        print(json.dumps(payload, indent=2, default=str))
