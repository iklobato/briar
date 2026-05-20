"""Output formatters (Strategy pattern + registry).

Adding a new format = one `Formatter` subclass + one entry in
`FORMATTERS`. Nothing else changes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from briar.formatting.columns import infer_columns, render_table
from briar.formatting.csv import FormatCsv
from briar.formatting.json import FormatJson
from briar.formatting.quiet import FormatQuiet
from briar.formatting.table import FormatTable
from briar.formatting.yaml import FormatYaml


class Formatter(Protocol):
    """Implementation contract. Format names live in `FORMATTERS`."""
    name: str

    def render(
        self,
        payload: Any,
        columns: Optional[List[str]] = None,
    ) -> None:
        ...


FORMATTERS: Dict[str, Formatter] = {
    "table": FormatTable(),
    "json":  FormatJson(),
    "yaml":  FormatYaml(),
    "csv":   FormatCsv(),
    "quiet": FormatQuiet(),
}


def render(
    payload: Any,
    format_name: str,
    columns: Optional[List[str]] = None,
) -> None:
    """Apply the named formatter, falling back to table on unknown."""
    fmt = FORMATTERS.get(format_name) or FORMATTERS["table"]
    fmt.render(payload, columns)


def render_object(payload: Any, format_name: str) -> None:
    """Single-record rendering — defaults to JSON for the `table`
    request because a single row table is just JSON with line noise."""
    effective = "json" if format_name == "table" else format_name
    render(payload, effective)


__all__ = [
    "Formatter",
    "FORMATTERS",
    "render",
    "render_object",
    "infer_columns",
    "render_table",
]
