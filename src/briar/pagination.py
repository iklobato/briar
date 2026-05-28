"""Payload-shape helpers used by the formatters.

Introspection over the dict / list / paginated-dict shapes the
extractors and formatters pass around. Two free functions — the
previous `Payload` static-only class wrapper added no behavior over
plain module access and obscured that these are just predicates."""

from __future__ import annotations

from typing import Any, Dict, List


def items_of(page: Any) -> List[Dict[str, Any]]:
    """Return a list of items regardless of whether the response is
    a paginated dict, a bare list, or a single object."""
    if isinstance(page, list):
        return page
    if isinstance(page, dict):
        results = page.get("results")
        if isinstance(results, list):
            return results
        return [page]
    return []


def looks_like_list(payload: Any) -> bool:
    if isinstance(payload, list):
        return True
    if isinstance(payload, dict):
        results = payload.get("results")
        return isinstance(results, list)
    return False


