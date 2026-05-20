"""Quiet formatter — one id per line, designed to pipe into `xargs`."""

from __future__ import annotations

from typing import Any, Dict, List

from briar.formatting.base import Formatter
from briar.pagination import Payload


class FormatQuiet(Formatter):
    name = "quiet"

    def render(
        self,
        payload: Any,
        columns: List[str] = [],
    ) -> None:
        items = Payload.items_of(payload) if Payload.looks_like_list(payload) else [self._to_dict(payload)]
        for it in items:
            row_id = it.get("id") if type(it) is dict else ""
            if row_id:
                print(row_id)

    @staticmethod
    def _to_dict(payload: Any) -> Dict[str, Any]:
        if type(payload) is dict:
            return payload
        return {}
