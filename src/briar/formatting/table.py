"""Table formatter — the default."""

from __future__ import annotations

import json
from typing import Any, List, Optional

from briar.formatting.columns import infer_columns, render_table
from briar.pagination import items_of, looks_like_list


class FormatTable:
    name = "table"

    def render(
        self,
        payload: Any,
        columns: Optional[List[str]] = None,
    ) -> None:
        if not looks_like_list(payload):
            # Single-record fallback — JSON is more useful than a one-row
            # table; the `render_object` wrapper enforces this path.
            print(json.dumps(payload, indent=2, default=str))
            return
        items = items_of(payload)
        render_table(items, columns or infer_columns(items))
