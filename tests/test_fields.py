"""Field parser tests — JSON / plain string / @file / stdin."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from unittest import mock

from briar.errors import CliError
from briar.fields import parse_fields


class ParseFieldsTests(unittest.TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(parse_fields(["name=demo"]), {"name": "demo"})

    def test_json_int(self) -> None:
        self.assertEqual(parse_fields(["count=42"]), {"count": 42})

    def test_json_list(self) -> None:
        self.assertEqual(
            parse_fields(['tags=["a","b"]']),
            {"tags": ["a", "b"]},
        )

    def test_json_bool(self) -> None:
        self.assertEqual(parse_fields(["enabled=true"]), {"enabled": True})

    def test_file_reference(self) -> None:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False,
        ) as f:
            f.write('{"k": 1}')
            path = f.name
        try:
            result = parse_fields([f"payload=@{path}"])
            self.assertEqual(result, {"payload": {"k": 1}})
        finally:
            os.unlink(path)

    def test_stdin_dash(self) -> None:
        with mock.patch("sys.stdin", io.StringIO("hunter2\n")):
            result = parse_fields(["value=-"])
        self.assertEqual(result, {"value": "hunter2"})

    def test_empty_key_rejected(self) -> None:
        with self.assertRaises(CliError):
            parse_fields(["=value"])

    def test_missing_equals_rejected(self) -> None:
        with self.assertRaises(CliError):
            parse_fields(["novalue"])

    def test_empty_input(self) -> None:
        self.assertEqual(parse_fields(None), {})
        self.assertEqual(parse_fields([]), {})


if __name__ == "__main__":
    unittest.main()
