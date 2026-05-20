"""Quiet formatter — one id per line, designed to pipe into `xargs`."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from briar.pagination import items_of, looks_like_list


def _to_dict(payload: Any) -> Dict[str, Any]:
    if type(payload) is dict:
        return payload
    return {}


class FormatQuiet:
    name = "quiet"

    def render(
        self,
        payload: Any,
        columns: Optional[List[str]] = None,
    ) -> None:
        items = (
            items_of(payload) if looks_like_list(payload)
            else [_to_dict(payload)]
        )
        for it in items:
            row_id = it.get("id") if type(it) is dict else ""
            if row_id:
                print(row_id)
