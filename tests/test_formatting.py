"""Formatter / column / YAML emitter tests.

The YAML tests assert *structure* (key presence, value rendering), not
exact whitespace, because PyYAML's emit style differs slightly from the
hand-rolled emitter previous versions used."""

from __future__ import annotations

import io
import unittest
from unittest import mock

import yaml

from briar.formatting import FORMATTERS, render_object
from briar.formatting.columns import cell, infer_columns
from briar.formatting.yaml import to_yaml


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


class YamlEmitterTests(unittest.TestCase):
    """We assert via parse-roundtrip — exact whitespace is PyYAML's call."""

    def _round_trip(self, value):
        return yaml.safe_load(to_yaml(value))

    def test_scalars(self) -> None:
        self.assertIsNone(self._round_trip(None))
        self.assertIs(self._round_trip(True), True)
        self.assertEqual(self._round_trip(42), 42)
        self.assertEqual(self._round_trip("simple"), "simple")

    def test_quote_reserved_word(self) -> None:
        # `yes` must round-trip as a string, not a bool.
        self.assertEqual(self._round_trip("yes"), "yes")

    def test_quote_empty(self) -> None:
        self.assertEqual(self._round_trip(""), "")

    def test_quote_string_with_space(self) -> None:
        self.assertEqual(self._round_trip("has space"), "has space")

    def test_dict_nested(self) -> None:
        data = {"a": 1, "b": {"c": 2}}
        self.assertEqual(self._round_trip(data), data)

    def test_list_of_dicts(self) -> None:
        data = [{"id": "x"}, {"id": "y"}]
        self.assertEqual(self._round_trip(data), data)

    def test_empty_collections(self) -> None:
        self.assertEqual(self._round_trip({}), {})
        self.assertEqual(self._round_trip([]), [])


class FormatterRegistryTests(unittest.TestCase):
    def test_all_formats_registered(self) -> None:
        for name in ("table", "json", "yaml", "csv", "quiet"):
            self.assertIn(name, FORMATTERS)

    def test_render_object_promotes_table_to_json(self) -> None:
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            render_object({"id": "x", "name": "n"}, "table")
        self.assertIn('"id": "x"', buf.getvalue())

    def test_quiet_emits_only_ids(self) -> None:
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            FORMATTERS["quiet"].render(
                [{"id": "row-1"}, {"id": "row-2"}],
            )
        self.assertEqual(buf.getvalue().strip().splitlines(),
                         ["row-1", "row-2"])

    def test_csv_emits_header_and_rows(self) -> None:
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            FORMATTERS["csv"].render(
                [{"id": "a", "name": "x"}, {"id": "b", "name": "y"}],
                ["id", "name"],
            )
        lines = [
            line for line in buf.getvalue().splitlines() if line
        ]
        self.assertEqual(lines[0], "id,name")
        self.assertEqual(lines[1], "a,x")
        self.assertEqual(lines[2], "b,y")


if __name__ == "__main__":
    unittest.main()
