"""Table formatter — the default. Backed by `rich`.

`rich` auto-detects non-TTY output and falls back to plain text (no
ANSI codes), so `briar --format table … | grep …` still works."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Sequence

from rich.box import SIMPLE
from rich.console import Console
from rich.table import Table

from briar.formatting.base import Formatter
from briar.pagination import items_of, looks_like_list


_PREFERRED_COLUMNS = (
    "id",
    "name",
    "title",
    "status",
    "kind",
    "scope",
    "created_at",
)


class FormatTable(Formatter):
    name = "table"

    def render(
        self,
        payload: Any,
        columns: Sequence[str] = (),
    ) -> None:
        if not looks_like_list(payload):
            # Single-record fallback — JSON is more useful than a one-row
            # table; the `render_object` wrapper enforces this path.
            print(json.dumps(payload, indent=2, default=str))
            return
        items = items_of(payload)
        self._render_table(items, list(columns) if columns else self._infer_columns(items))

    @staticmethod
    def _cell(value: Any) -> str:
        """Render a single cell. Containers collapse to one line."""
        if value is None:
            return "-"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, dict):
            return json.dumps(value, default=str)[:40]
        if isinstance(value, list):
            return f"[{len(value)}]"
        return str(value)

    @staticmethod
    def _infer_columns(items: List[Dict[str, Any]]) -> List[str]:
        """Pick up to six columns: preferred ones first, then leftovers."""
        if not items:
            return []
        first = items[0]
        keys = list(first.keys()) if isinstance(first, dict) else []
        chosen = [k for k in _PREFERRED_COLUMNS if k in keys]
        extras = [k for k in keys if k not in chosen]
        return (chosen + extras)[:6]

    def _render_table(
        self,
        items: List[Dict[str, Any]],
        columns: List[str],
    ) -> None:
        console = Console(file=sys.stdout)
        if not items:
            console.print("(no rows)", style="dim")
            return
        if not columns:
            console.print_json(json.dumps(items, default=str))
            return

        table = Table(
            box=SIMPLE,
            show_header=True,
            header_style="bold cyan",
            show_edge=False,
            pad_edge=False,
            padding=(0, 1),
        )
        for c in columns:
            table.add_column(c, overflow="fold")
        for it in items:
            row = [self._cell(it.get(c)) for c in columns] if isinstance(it, dict) else [str(it)]
            table.add_row(*row)
        console.print(table)
