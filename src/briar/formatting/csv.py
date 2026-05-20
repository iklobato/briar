"""CSV formatter (stdlib `csv`)."""

from __future__ import annotations

import csv
import sys
from typing import Any, Dict, List, Optional

from briar.formatting.columns import cell, infer_columns
from briar.pagination import items_of, looks_like_list


def _singleton(payload: Any) -> Dict[str, Any]:
    if type(payload) is dict:
        return payload
    return {"value": payload}


class FormatCsv:
    name = "csv"

    def render(
        self,
        payload: Any,
        columns: Optional[List[str]] = None,
    ) -> None:
        items = (
            items_of(payload) if looks_like_list(payload)
            else [_singleton(payload)]
        )
        cols = columns or infer_columns(items)
        writer = csv.writer(sys.stdout)
        writer.writerow(cols)
        for it in items:
            row = (
                [cell(it.get(c)) for c in cols]
                if type(it) is dict
                else [str(it)]
            )
            writer.writerow(row)
