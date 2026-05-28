"""Formatters — table/json/yaml/csv/quiet.

Existing test_formatting.py covers basics; this module tests the
fall-through paths, cell-rendering edge cases, and unicode."""

from __future__ import annotations

import csv as csv_module
import json
from io import StringIO
from typing import Any

import pytest
import yaml as yaml_module
from hypothesis import given, strategies as st

from briar.formatting import FormatterRegistry, render, render_object
from briar.formatting.csv import FormatCsv
from briar.formatting.quiet import FormatQuiet
from briar.formatting.table import FormatTable
from briar.formatting.yaml import FormatYaml


class TestRegistry:
    @pytest.mark.parametrize("name", ["", "unknown", "TABLE"])
    def test_get_unknown_raises(self, name: str) -> None:
        # get() deliberately raises (not silently falls back to table) so
        # a typo like `--format yam` is loud at dispatch. Case-sensitive:
        # `TABLE` is unknown.
        from briar.errors import CliError

        with pytest.raises(CliError, match="unknown format"):
            FormatterRegistry.get(name)

    def test_render_object_promotes_table_to_json(self, capsys) -> None:
        render_object({"id": "x", "name": "y"}, "table")
        out = capsys.readouterr().out
        # JSON output is multi-line; table would be one-row.
        assert json.loads(out) == {"id": "x", "name": "y"}

    def test_render_object_passthrough_for_non_table(self, capsys) -> None:
        render_object({"a": 1}, "yaml")
        out = capsys.readouterr().out
        assert yaml_module.safe_load(out) == {"a": 1}

    def test_names_returns_all_registered(self) -> None:
        assert set(FormatterRegistry.names()) == {"table", "json", "yaml", "csv", "quiet"}


class TestTableCell:
    def test_none_renders_as_dash(self) -> None:
        assert FormatTable._cell(None) == "-"

    @pytest.mark.parametrize("val,expected", [(True, "true"), (False, "false")])
    def test_bool_lowercase(self, val: bool, expected: str) -> None:
        assert FormatTable._cell(val) == expected

    def test_dict_truncated_at_40_chars(self) -> None:
        big = {"k": "x" * 100}
        out = FormatTable._cell(big)
        assert len(out) <= 40

    def test_list_renders_as_bracket_length(self) -> None:
        assert FormatTable._cell([1, 2, 3]) == "[3]"
        assert FormatTable._cell([]) == "[0]"

    def test_other_types_stringified(self) -> None:
        assert FormatTable._cell(42) == "42"
        assert FormatTable._cell(3.14) == "3.14"


class TestColumnInference:
    def test_empty_items_empty_columns(self) -> None:
        assert FormatTable._infer_columns([]) == []

    def test_preferred_columns_first(self) -> None:
        items = [{"name": "n", "id": "i", "extra": "e", "title": "t"}]
        cols = FormatTable._infer_columns(items)
        # Preferred ordering: id, name, title; then extras.
        assert cols.index("id") < cols.index("name") < cols.index("title")
        assert "extra" in cols

    def test_limits_to_six_columns(self) -> None:
        items = [{f"col{i}": i for i in range(20)}]
        assert len(FormatTable._infer_columns(items)) == 6

    def test_first_item_only_documented(self) -> None:
        # Second item's extra key is invisible to column inference.
        items = [{"id": "a"}, {"id": "b", "extra": "x"}]
        cols = FormatTable._infer_columns(items)
        assert "extra" not in cols


class TestTableRender:
    def test_non_list_payload_renders_as_json(self, capsys) -> None:
        render({"a": 1}, "table")
        out = capsys.readouterr().out
        assert json.loads(out) == {"a": 1}

    def test_empty_list_says_no_rows(self, capsys) -> None:
        render([], "table")
        out = capsys.readouterr().out
        assert "no rows" in out


class TestCsv:
    def _parse_csv(self, text: str) -> list[list[str]]:
        return list(csv_module.reader(StringIO(text)))

    def test_singleton_wraps_non_dict_under_value(self) -> None:
        assert FormatCsv._singleton(42) == {"value": 42}
        assert FormatCsv._singleton("s") == {"value": "s"}

    def test_singleton_passes_dict_through(self) -> None:
        d = {"k": "v"}
        assert FormatCsv._singleton(d) is d

    def test_unicode_in_cells_handled(self, capsys) -> None:
        render([{"id": "ñ", "name": "日本語"}], "csv")
        out = capsys.readouterr().out
        rows = self._parse_csv(out)
        assert "ñ" in rows[1]
        assert "日本語" in rows[1]

    def test_missing_column_in_item_renders_as_dash(self, capsys) -> None:
        render([{"id": "a"}, {"id": "b", "extra": "x"}], "csv", columns=["id", "extra"])
        out = capsys.readouterr().out
        rows = self._parse_csv(out)
        # Header + 2 rows
        assert rows[0] == ["id", "extra"]
        assert rows[1] == ["a", "-"]
        assert rows[2] == ["b", "x"]


class TestYaml:
    def test_preserves_insertion_order(self) -> None:
        out = FormatYaml.to_yaml({"z": 1, "a": 2, "m": 3})
        # sort_keys=False — keys appear in insertion order
        assert out.index("z") < out.index("a") < out.index("m")

    @pytest.mark.parametrize("word", ["yes", "no", "on", "off", "true", "false"])
    def test_yaml_reserved_words_quoted_when_strings(self, word: str) -> None:
        out = FormatYaml.to_yaml({"val": word})
        # Parsing the output must give back a string, not a bool.
        loaded = yaml_module.safe_load(out)
        assert loaded == {"val": word}, f"reserved word {word!r} not preserved as string"

    def test_unicode_preserved(self) -> None:
        out = FormatYaml.to_yaml({"k": "日本語"})
        assert "日本語" in out

    def test_render_strips_trailing_newline(self, capsys) -> None:
        render({"a": 1}, "yaml")
        out = capsys.readouterr().out
        # print() adds one newline; should be exactly one
        assert out.count("\n") == 1


class TestQuiet:
    def test_prints_truthy_ids_one_per_line(self, capsys) -> None:
        render([{"id": "a"}, {"id": "b"}], "quiet")
        out = capsys.readouterr().out
        assert out.splitlines() == ["a", "b"]

    @pytest.mark.parametrize("falsy", [None, "", 0, False])
    def test_falsy_ids_skipped(self, capsys, falsy: object) -> None:
        render([{"id": falsy}], "quiet")
        assert capsys.readouterr().out == ""

    def test_string_zero_truthy_printed(self, capsys) -> None:
        render([{"id": "0"}], "quiet")
        assert capsys.readouterr().out == "0\n"

    def test_no_id_key_skipped(self, capsys) -> None:
        render([{"name": "x"}], "quiet")
        assert capsys.readouterr().out == ""

    def test_to_dict_non_dict_returns_empty(self) -> None:
        assert FormatQuiet._to_dict("string") == {}
        assert FormatQuiet._to_dict(42) == {}


class TestJson:
    def test_uses_default_str_for_unserialisable(self, capsys) -> None:
        # `object()` is not JSON-serialisable by default; default=str saves us.
        render([{"obj": object()}], "json")
        out = capsys.readouterr().out
        assert "object at" in out


# Restrict text to printable ASCII to avoid YAML-spec normalisation of
# line-separator code points (NEL \x85, LS  , PS  ). Those are
# YAML behaviours we don't control; they would cause spurious roundtrip
# failures unrelated to our formatters.
_KEYS = st.text(alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E), min_size=1, max_size=10)
_VALS = st.one_of(st.none(), st.booleans(), st.integers(), st.text(alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E), max_size=20))
_ROWS = st.lists(st.dictionaries(_KEYS, _VALS, max_size=5), max_size=10)


@pytest.mark.property
class TestRoundtrip:
    @given(rows=_ROWS)
    def test_json_roundtrip(self, rows: list[dict[str, Any]]) -> None:
        # Use the static `to_yaml` analog: json.dumps directly.
        out = json.dumps(rows, indent=2, default=str)
        assert json.loads(out) == rows

    @given(rows=_ROWS)
    def test_yaml_roundtrip(self, rows: list[dict[str, Any]]) -> None:
        out = FormatYaml.to_yaml(rows)
        assert yaml_module.safe_load(out) == rows
