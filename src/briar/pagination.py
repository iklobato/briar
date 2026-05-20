"""Payload-shape helpers used by the formatters.

The HTTP/API layer that produced these shapes is gone, but extractors
and formatters still need to normalise list-vs-object payloads without
`isinstance`."""

from __future__ import annotations

from typing import Any, Dict, List


def items_of(page: Any) -> List[Dict[str, Any]]:
    """Return a list of items regardless of whether the response is a
    paginated dict, a bare list, or a single object."""
    if type(page) is list:
        return page
    if type(page) is dict:
        results = page.get("results")
        if type(results) is list:
            return results
        return [page]
    return []


def looks_like_list(payload: Any) -> bool:
    if type(payload) is list:
        return True
    if type(payload) is dict:
        results = payload.get("results")
        return type(results) is list
    return False
