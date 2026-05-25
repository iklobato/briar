"""`briar context` — CRUD over knowledge blobs."""

from __future__ import annotations

import io

import pytest


class TestContextPut:
    def test_put_from_inline_content(self, cli, tmp_root) -> None:
        result = cli("context", "--root", str(tmp_root / "knowledge"), "put", "knowledge:acme", "--content", "hello")
        assert result.code == 0
        # File written
        assert (tmp_root / "knowledge" / "knowledge" / "acme.md").read_text() == "hello"

    def test_put_from_file(self, cli, tmp_root) -> None:
        src = tmp_root / "src.md"
        src.write_text("from-file")
        result = cli("context", "--root", str(tmp_root / "knowledge"), "put", "knowledge:acme", "--from-file", str(src))
        assert result.code == 0
        assert (tmp_root / "knowledge" / "knowledge" / "acme.md").read_text() == "from-file"

    def test_put_from_stdin_dash_content(self, cli, tmp_root, monkeypatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("from-stdin"))
        result = cli("context", "--root", str(tmp_root / "knowledge"), "put", "knowledge:acme", "--content", "-")
        assert result.code == 0
        assert (tmp_root / "knowledge" / "knowledge" / "acme.md").read_text() == "from-stdin"


class TestContextGet:
    def test_get_existing_blob_to_stdout(self, cli, tmp_root) -> None:
        cli("context", "--root", str(tmp_root / "knowledge"), "put", "knowledge:acme", "--content", "body-here")
        result = cli("context", "--root", str(tmp_root / "knowledge"), "get", "knowledge:acme")
        assert result.code == 0
        assert "body-here" in result.out

    def test_get_appends_trailing_newline(self, cli, tmp_root) -> None:
        cli("context", "--root", str(tmp_root / "knowledge"), "put", "knowledge:acme", "--content", "no-newline")
        result = cli("context", "--root", str(tmp_root / "knowledge"), "get", "knowledge:acme")
        assert result.out.endswith("\n")

    def test_get_missing_raises_clierror_exit_1(self, cli, tmp_root) -> None:
        result = cli("context", "--root", str(tmp_root / "knowledge"), "get", "knowledge:missing")
        assert result.code == 1
        assert "not found" in result.err


class TestContextDelete:
    def test_delete_with_yes_succeeds(self, cli, tmp_root) -> None:
        cli("context", "--root", str(tmp_root / "knowledge"), "put", "knowledge:acme", "--content", "x")
        result = cli("context", "--root", str(tmp_root / "knowledge"), "delete", "knowledge:acme", "--yes")
        assert result.code == 0
        assert "deleted" in result.out

    def test_delete_missing_without_yes_aborts_returns_1(self, cli, tmp_root, monkeypatch) -> None:
        # Simulate user pressing N at prompt.
        monkeypatch.setattr("builtins.input", lambda _: "n")
        result = cli("context", "--root", str(tmp_root / "knowledge"), "delete", "knowledge:missing")
        assert result.code == 1
        assert "aborted" in result.out

    def test_delete_with_yes_input_proceeds(self, cli, tmp_root, monkeypatch) -> None:
        cli("context", "--root", str(tmp_root / "knowledge"), "put", "knowledge:acme", "--content", "x")
        monkeypatch.setattr("builtins.input", lambda _: "y")
        result = cli("context", "--root", str(tmp_root / "knowledge"), "delete", "knowledge:acme")
        assert result.code == 0
        assert "deleted" in result.out


class TestContextList:
    def test_list_filter_by_prefix(self, cli, tmp_root) -> None:
        for name in ("knowledge:acme", "lessons:python", "knowledge:other"):
            cli("context", "--root", str(tmp_root / "knowledge"), "put", name, "--content", "x")
        result = cli("--format", "json", "context", "--root", str(tmp_root / "knowledge"), "list", "--prefix", "knowledge:")
        assert result.code == 0
        # Both knowledge:* blobs present, lessons:* absent
        assert "knowledge:acme" in result.out
        assert "knowledge:other" in result.out
        assert "lessons" not in result.out

    def test_list_no_blobs_shows_no_rows(self, cli, tmp_root) -> None:
        result = cli("context", "--root", str(tmp_root / "knowledge"), "list")
        assert result.code == 0


class TestContextCategories:
    def test_categories_groups_blobs_by_prefix(self, cli, tmp_root) -> None:
        for name in ("knowledge:a", "knowledge:b", "lessons:x"):
            cli("context", "--root", str(tmp_root / "knowledge"), "put", name, "--content", "x")
        result = cli("--format", "json", "context", "--root", str(tmp_root / "knowledge"), "categories")
        assert result.code == 0
        assert "knowledge" in result.out
        assert "lessons" in result.out


class TestContextErrorRouting:
    def test_unknown_subcommand_argparse_exit_2(self, cli, tmp_root) -> None:
        result = cli("context", "--root", str(tmp_root / "knowledge"), "unknown-op")
        assert result.code == 2
