"""Pagination + payload-shape helpers.

DRF returns `{count, next, previous, results}` for list endpoints and
plain JSON otherwise. These helpers shield callers from the variance
without using `isinstance`."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


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


def next_of(page: Any) -> Optional[str]:
    if type(page) is dict:
        return page.get("next") or None
    return None


def to_relative(url: str, api_base: str) -> str:
    """DRF `next` is a full URL; strip the api_base so the client
    can re-issue against its own host."""
    prefix = api_base.rstrip("/")
    if url.startswith(prefix):
        return url[len(prefix):]
    return url


def looks_like_list(payload: Any) -> bool:
    """`True` for both `[…]` and `{"results": [...]}` shapes."""
    if type(payload) is list:
        return True
    if type(payload) is dict:
        return "results" in payload
    return False
