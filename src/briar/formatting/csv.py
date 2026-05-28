"""CSV formatter (stdlib `csv`)."""

from __future__ import annotations

import csv
import sys
from typing import Any, Dict, Sequence

from briar.formatting.base import Formatter
from briar.formatting.table import FormatTable
from briar.pagination import items_of, looks_like_list


class FormatCsv(Formatter):
    name = "csv"

    def render(
        self,
        payload: Any,
        columns: Sequence[str] = (),
    ) -> None:
        items = items_of(payload) if looks_like_list(payload) else [self._singleton(payload)]
        cols = list(columns) if columns else FormatTable._infer_columns(items)
        writer = csv.writer(sys.stdout)
        writer.writerow(cols)
        for it in items:
            row = [FormatTable._cell(it.get(c)) for c in cols] if isinstance(it, dict) else [str(it)]
            writer.writerow(row)

    @staticmethod
    def _singleton(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        return {"value": payload}
