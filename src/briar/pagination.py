"""Payload-shape helpers used by the formatters.

`Payload` is a static-only namespace — never instantiated. Keeping it
class-bound (rather than as free functions) lets every caller import
one symbol and discover the related helpers via attribute access."""

from __future__ import annotations

from typing import Any, Dict, List


class Payload:
    """Introspection over the dict / list / paginated-dict shapes the
    extractors and formatters pass around. Pure static methods — no
    state, no instances."""

    @staticmethod
    def items_of(page: Any) -> List[Dict[str, Any]]:
        """Return a list of items regardless of whether the response is
        a paginated dict, a bare list, or a single object."""
        if type(page) is list:
            return page
        if type(page) is dict:
            results = page.get("results")
            if type(results) is list:
                return results
            return [page]
        return []

    @staticmethod
    def looks_like_list(payload: Any) -> bool:
        if type(payload) is list:
            return True
        if type(payload) is dict:
            results = payload.get("results")
            return type(results) is list
        return False
