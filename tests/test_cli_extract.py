"""Global-flag pre-extractor tests."""

from __future__ import annotations

import unittest

from briar.cli import _extract_global_flags
from briar.errors import CliError


class ExtractTests(unittest.TestCase):
    def test_flag_before_subcommand(self) -> None:
        globals_kv, rest = _extract_global_flags(
            ["--format", "yaml", "agents", "list"]
        )
        self.assertEqual(globals_kv, {"--format": "yaml"})
        self.assertEqual(rest, ["agents", "list"])

    def test_flag_after_subcommand(self) -> None:
        globals_kv, rest = _extract_global_flags(
            ["agents", "list", "--format", "yaml"]
        )
        self.assertEqual(globals_kv, {"--format": "yaml"})
        self.assertEqual(rest, ["agents", "list"])

    def test_equals_form(self) -> None:
        globals_kv, rest = _extract_global_flags(
            ["agents", "list", "--profile=acme"]
        )
        self.assertEqual(globals_kv, {"--profile": "acme"})
        self.assertEqual(rest, ["agents", "list"])

    def test_mixed(self) -> None:
        globals_kv, rest = _extract_global_flags([
            "--profile", "acme",
            "--workspace=ws-1",
            "agents", "list", "--limit", "5",
            "--format", "json",
        ])
        self.assertEqual(globals_kv, {
            "--profile": "acme",
            "--workspace": "ws-1",
            "--format": "json",
        })
        self.assertEqual(rest, ["agents", "list", "--limit", "5"])

    def test_dangling_flag_rejected(self) -> None:
        with self.assertRaises(CliError):
            _extract_global_flags(["--format"])


if __name__ == "__main__":
    unittest.main()
