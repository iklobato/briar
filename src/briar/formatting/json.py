"""JSON formatter."""

from __future__ import annotations

import json
from typing import Any, List

from briar.formatting.base import Formatter


class FormatJson(Formatter):
    name = "json"

    def render(
        self,
        payload: Any,
        columns: List[str] = [],
    ) -> None:
        print(json.dumps(payload, indent=2, default=str))
