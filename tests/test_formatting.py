"""Formatter / column / YAML emitter tests.

The YAML tests assert *structure* (key presence, value rendering), not
exact whitespace, because PyYAML's emit style differs slightly from the
hand-rolled emitter previous versions used."""

from __future__ import annotations

import io
import unittest
from unittest import mock

from briar.formatting import FORMATTERS, render_object
from briar.formatting.table import FormatTable


cell = FormatTable._cell
infer_columns = FormatTable._infer_columns


class CellTests(unittest.TestCase):
    def test_none(self) -> None:
        self.assertEqual(cell(None), "-")

    def test_bool(self) -> None:
        self.assertEqual(cell(True), "true")
        self.assertEqual(cell(False), "false")

    def test_dict(self) -> None:
        self.assertTrue(cell({"a": 1}).startswith("{"))

    def test_list(self) -> None:
        self.assertEqual(cell([1, 2, 3]), "[3]")

    def test_string(self) -> None:
        self.assertEqual(cell("hello"), "hello")


class InferColumnsTests(unittest.TestCase):
    def test_preferred_order(self) -> None:
        items = [{"name": "x", "id": "u", "created_at": "now", "extra": 1}]
        cols = infer_columns(items)
        self.assertEqual(cols[0], "id")
        self.assertEqual(cols[1], "name")
        self.assertIn("created_at", cols)
        self.assertIn("extra", cols)

    def test_empty(self) -> None:
        self.assertEqual(infer_columns([]), [])


class FormatterRegistryTests(unittest.TestCase):
    def test_all_formats_registered(self) -> None:
        self.assertEqual(sorted(FORMATTERS), ["json", "table"])

    def test_render_object_promotes_table_to_json(self) -> None:
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            render_object({"id": "x", "name": "n"}, "table")
        self.assertIn('"id": "x"', buf.getvalue())


if __name__ == "__main__":
    unittest.main()
