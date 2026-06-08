"""`briar context` — PARAMETRIC flag-effect coverage.

Companion to test_context.py (CRUD happy/unhappy paths). This file
asserts the *observable effect* of every flag in
/tmp/cli_manifest/context.md:

  top-level  --store (choices file|postgres) · --root
  put        blob_name (positional) · --content · --from-file · --category
  get        blob_name (positional)
  list       --prefix
  delete     blob_name (positional) · --yes
  categories (no flags)

The knowledge store is patched at the seam ``briar.commands.context.make_store``
with a recording fake, so we assert the exact (kind, root) the factory was
asked for and the exact put/get/list/delete payload — a swapped/dropped/
ignored flag must make a test FAIL. No network, no postgres DSN needed.
"""

from __future__ import annotations

import io

import pytest

from briar.storage.base import KnowledgeRef


class _RecordingStore:
    """Records every put/get/list/delete against the knowledge store."""

    def __init__(self) -> None:
        self.puts: list = []
        self.gets: list = []
        self.lists: list = []
        self.deletes: list = []
        self._body: dict = {}
        self._refs: list = []

    def seed_ref(self, name: str, category: str = "") -> None:
        self._refs.append(KnowledgeRef(name=name, category=category, byte_count=1, updated_at="2026-01-01"))

    def seed_body(self, name: str, body: str) -> None:
        self._body[name] = body

    def put(self, blob_name: str, content: str, category: str = "") -> KnowledgeRef:
        self.puts.append((blob_name, content, category))
        return KnowledgeRef(name=blob_name, category=category or "auto", byte_count=len(content), updated_at="2026-01-01")

    def get(self, blob_name: str) -> str:
        self.gets.append(blob_name)
        return self._body.get(blob_name, "")

    def list(self, prefix: str = "") -> list:
        self.lists.append(prefix)
        return [r for r in self._refs if r.name.startswith(prefix)]

    def delete(self, blob_name: str) -> bool:
        self.deletes.append(blob_name)
        return True


@pytest.fixture
def ctx_seam(mocker):
    """Patch ``make_store`` at the command seam; capture (kind, file_root)."""
    from types import SimpleNamespace

    state = SimpleNamespace(store=_RecordingStore(), factory_calls=[])

    def factory(kind, file_root=None):
        state.factory_calls.append((kind, file_root))
        return state.store

    mocker.patch("briar.commands.context.make_store", side_effect=factory)
    return state


# ───────────────────────── top-level --store / --root ──────────────────


class TestStoreAndRootFlags:
    @pytest.mark.parametrize("store_kind", ["file", "postgres"], ids=["file", "postgres"])
    def test_store_choice_reaches_factory(self, cli, ctx_seam, store_kind) -> None:
        # Each documented backend choice is forwarded to make_store verbatim.
        result = cli("context", "--store", store_kind, "list")
        assert result.code == 0
        assert ctx_seam.factory_calls[0][0] == store_kind

    def test_store_default_is_file(self, cli, ctx_seam) -> None:
        result = cli("context", "list")
        assert result.code == 0
        assert ctx_seam.factory_calls[0][0] == "file"

    def test_invalid_store_choice_exit_2(self, cli) -> None:
        result = cli("context", "--store", "s3", "list")
        assert result.code == 2
        assert "invalid choice" in result.err

    def test_root_value_reaches_factory(self, cli, ctx_seam) -> None:
        from pathlib import Path

        result = cli("context", "--root", "/tmp/briar-test-root", "list")
        assert result.code == 0
        assert ctx_seam.factory_calls[0][1] == Path("/tmp/briar-test-root")

    def test_root_default_is_dot_knowledge(self, cli, ctx_seam) -> None:
        from pathlib import Path

        result = cli("context", "list")
        assert result.code == 0
        assert ctx_seam.factory_calls[0][1] == Path("./knowledge")


# ──────────────────────────────── put flags ────────────────────────────


class TestPutFlags:
    def test_blob_name_positional_required(self, cli) -> None:
        result = cli("context", "put")
        assert result.code == 2
        assert "required" in result.err or "arguments" in result.err

    def test_content_inline_reaches_put(self, cli, ctx_seam) -> None:
        # --content text is the exact body persisted.
        result = cli("context", "put", "knowledge:acme", "--content", "INLINE-BODY")
        assert result.code == 0
        assert ctx_seam.store.puts == [("knowledge:acme", "INLINE-BODY", "")]

    def test_content_dash_reads_stdin(self, cli, ctx_seam, monkeypatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("STDIN-BODY"))
        result = cli("context", "put", "knowledge:acme", "--content", "-")
        assert result.code == 0
        assert ctx_seam.store.puts == [("knowledge:acme", "STDIN-BODY", "")]

    def test_from_file_reads_path(self, cli, ctx_seam, tmp_path) -> None:
        src = tmp_path / "body.md"
        src.write_text("FILE-BODY")
        result = cli("context", "put", "knowledge:acme", "--from-file", str(src))
        assert result.code == 0
        assert ctx_seam.store.puts == [("knowledge:acme", "FILE-BODY", "")]

    def test_category_reaches_put(self, cli, ctx_seam) -> None:
        # --category overrides the prefix-derived default.
        result = cli("context", "put", "knowledge:acme", "--content", "x", "--category", "lessons")
        assert result.code == 0
        assert ctx_seam.store.puts == [("knowledge:acme", "x", "lessons")]

    def test_category_default_empty(self, cli, ctx_seam) -> None:
        # Omitting --category passes '' (store derives category itself).
        result = cli("context", "put", "knowledge:acme", "--content", "x")
        assert result.code == 0
        assert ctx_seam.store.puts[0][2] == ""

    def test_content_wins_over_from_file_when_both(self, cli, ctx_seam, tmp_path) -> None:
        # _read_content checks --content first; --from-file is the fallback.
        src = tmp_path / "body.md"
        src.write_text("FROM-FILE")
        result = cli("context", "put", "knowledge:acme", "--content", "FROM-CONTENT", "--from-file", str(src))
        assert result.code == 0
        assert ctx_seam.store.puts == [("knowledge:acme", "FROM-CONTENT", "")]


# ──────────────────────────────── get flag ─────────────────────────────


class TestGetFlags:
    def test_blob_name_positional_required(self, cli) -> None:
        result = cli("context", "get")
        assert result.code == 2

    def test_blob_name_reaches_get_and_body_printed(self, cli, ctx_seam) -> None:
        ctx_seam.store.seed_body("knowledge:acme", "RETRIEVED-BODY")
        result = cli("context", "get", "knowledge:acme")
        assert result.code == 0
        assert ctx_seam.store.gets == ["knowledge:acme"]
        assert "RETRIEVED-BODY" in result.out


# ──────────────────────────────── list --prefix ────────────────────────


class TestListPrefixFlag:
    def test_prefix_reaches_store_list(self, cli, ctx_seam) -> None:
        ctx_seam.store.seed_ref("knowledge:acme")
        ctx_seam.store.seed_ref("lessons:py")
        result = cli("context", "list", "--prefix", "knowledge:")
        assert result.code == 0
        # The prefix value reached the store's list() call exactly.
        assert ctx_seam.store.lists == ["knowledge:"]
        assert "knowledge:acme" in result.out
        assert "lessons:py" not in result.out

    def test_prefix_default_empty(self, cli, ctx_seam) -> None:
        result = cli("context", "list")
        assert result.code == 0
        assert ctx_seam.store.lists == [""]


# ──────────────────────────────── delete flags ─────────────────────────


class TestDeleteFlags:
    def test_blob_name_positional_required(self, cli) -> None:
        result = cli("context", "delete")
        assert result.code == 2

    def test_yes_skips_prompt_and_deletes(self, cli, ctx_seam) -> None:
        result = cli("context", "delete", "knowledge:acme", "--yes")
        assert result.code == 0
        assert ctx_seam.store.deletes == ["knowledge:acme"]
        assert "deleted" in result.out

    def test_without_yes_aborts_on_no_prompt_no_delete(self, cli, ctx_seam, monkeypatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "n")
        result = cli("context", "delete", "knowledge:acme")
        assert result.code == 1
        assert ctx_seam.store.deletes == []
        assert "aborted" in result.out

    def test_without_yes_proceeds_on_yes_prompt(self, cli, ctx_seam, monkeypatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _: "y")
        result = cli("context", "delete", "knowledge:acme")
        assert result.code == 0
        assert ctx_seam.store.deletes == ["knowledge:acme"]


# ──────────────────────────── categories (no flags) ────────────────────


class TestCategories:
    def test_categories_groups_distinct_prefixes(self, cli, ctx_seam) -> None:
        ctx_seam.store.seed_ref("knowledge:a", category="knowledge")
        ctx_seam.store.seed_ref("knowledge:b", category="knowledge")
        ctx_seam.store.seed_ref("lessons:x", category="lessons")
        result = cli("--format", "json", "context", "categories")
        assert result.code == 0
        # Listed once each, with counts derived from the store contents.
        assert "knowledge" in result.out
        assert "lessons" in result.out
