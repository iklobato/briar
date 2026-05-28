"""Output formatters (Strategy + Registry).

Adding a new format = one `Formatter` subclass + one entry in
`FormatterRegistry.FORMATTERS`. Nothing else changes."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from briar.errors import CliError
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
        "json": FormatJson(),
        "yaml": FormatYaml(),
        "csv": FormatCsv(),
        "quiet": FormatQuiet(),
    }

    @classmethod
    def names(cls) -> List[str]:
        return list(cls.FORMATTERS.keys())

    @classmethod
    def get(cls, format_name: str) -> Formatter:
        """Resolve a format by name. Unknown name raises CliError so a
        typo (``--format yam``) is loud at dispatch instead of silently
        falling back to the table renderer."""
        formatter = cls.FORMATTERS.get(format_name)
        if formatter is None:
            known = ", ".join(sorted(cls.FORMATTERS))
            raise CliError(f"unknown format {format_name!r}; known: {known}")
        return formatter

    @classmethod
    def render(
        cls,
        payload: Any,
        format_name: str,
        columns: Sequence[str] = (),
    ) -> None:
        cls.get(format_name).render(payload, columns)

    @classmethod
    def render_object(cls, payload: Any, format_name: str) -> None:
        """Single-record path. Each Formatter handles single-record
        payloads itself (FormatTable falls through to JSON internally;
        every other formatter renders the object naturally). The
        previous shape's "table → json" string-dispatch duplicated
        FormatTable's own single-record branch."""
        cls.render(payload, format_name)


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
