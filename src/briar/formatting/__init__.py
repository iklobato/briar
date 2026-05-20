"""Output formatters (Strategy + Registry).

Adding a new format = one `Formatter` subclass + one entry in
`FormatterRegistry.FORMATTERS`. Nothing else changes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from briar.formatting.base import Formatter
from briar.formatting.csv import FormatCsv
from briar.formatting.json import FormatJson
from briar.formatting.quiet import FormatQuiet
from briar.formatting.table import FormatTable
from briar.formatting.yaml import FormatYaml


class FormatterRegistry:
    """Strategy lookup + entry points. Static methods only — there is
    no instance state worth carrying."""

    FORMATTERS: Dict[str, Formatter] = {
        "table": FormatTable(),
        "json":  FormatJson(),
        "yaml":  FormatYaml(),
        "csv":   FormatCsv(),
        "quiet": FormatQuiet(),
    }

    @classmethod
    def names(cls) -> List[str]:
        return list(cls.FORMATTERS.keys())

    @classmethod
    def get(cls, format_name: str) -> Formatter:
        return cls.FORMATTERS.get(format_name) or cls.FORMATTERS["table"]

    @classmethod
    def render(
        cls,
        payload: Any,
        format_name: str,
        columns: Optional[List[str]] = None,
    ) -> None:
        cls.get(format_name).render(payload, columns)

    @classmethod
    def render_object(cls, payload: Any, format_name: str) -> None:
        """Single-record path: `table` falls through to JSON because a
        one-row table is just JSON with line noise."""
        effective = "json" if format_name == "table" else format_name
        cls.render(payload, effective)


# Module-level callables — keep the existing import surface stable.
FORMATTERS = FormatterRegistry.FORMATTERS
render = FormatterRegistry.render
render_object = FormatterRegistry.render_object


__all__ = [
    "Formatter",
    "FormatterRegistry",
    "FORMATTERS",
    "render",
    "render_object",
]
