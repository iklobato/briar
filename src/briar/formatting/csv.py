"""CSV formatter (stdlib `csv`)."""

from __future__ import annotations

import csv
import sys
from typing import Any, Dict, List

from briar.formatting.base import Formatter
from briar.formatting.table import FormatTable
from briar.pagination import Payload


class FormatCsv(Formatter):
    name = "csv"

    def render(
        self,
        payload: Any,
        columns: List[str] = [],
    ) -> None:
        items = Payload.items_of(payload) if Payload.looks_like_list(payload) else [self._singleton(payload)]
        cols = columns or FormatTable._infer_columns(items)
        writer = csv.writer(sys.stdout)
        writer.writerow(cols)
        for it in items:
            row = [FormatTable._cell(it.get(c)) for c in cols] if type(it) is dict else [str(it)]
            writer.writerow(row)

    @staticmethod
    def _singleton(payload: Any) -> Dict[str, Any]:
        if type(payload) is dict:
            return payload
        return {"value": payload}
