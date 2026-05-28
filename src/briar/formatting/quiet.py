"""Quiet formatter — one id per line, designed to pipe into `xargs`."""

from __future__ import annotations

from typing import Any, Dict, Sequence

from briar.formatting.base import Formatter
from briar.pagination import items_of, looks_like_list


class FormatQuiet(Formatter):
    name = "quiet"

    def render(
        self,
        payload: Any,
        columns: Sequence[str] = (),
    ) -> None:
        items = items_of(payload) if looks_like_list(payload) else [self._to_dict(payload)]
        for it in items:
            row_id = it.get("id") if isinstance(it, dict) else ""
            if row_id:
                print(row_id)

    @staticmethod
    def _to_dict(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        return {}
