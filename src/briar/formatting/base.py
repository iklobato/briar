"""Abstract base for every output formatter.

Concretes must subclass `Formatter` and implement `render`. The
registry in `__init__.py` enforces the nominal type — Protocol-style
structural typing was previously in use, which compiled but didn't
catch a missing method until call-time."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List, Optional


class Formatter(ABC):
    """Output-formatter Strategy contract."""

    name: str = ""

    @abstractmethod
    def render(
        self,
        payload: Any,
        columns: Optional[List[str]] = None,
    ) -> None:
        """Write the rendered payload to stdout."""
