"""KnowledgeStore parity contract — exercised against every backend.

The `store` fixture in tests/conftest.py is parametrized over backends
(`file` default, `postgres` opt-in via BRIAR_TEST_PG_DSN env var). Each
test must pass on every backend — that's the contract."""

from __future__ import annotations

import hashlib

import pytest

from briar.storage.base import KnowledgeRef


class TestRoundtrip:
    def test_put_get_returns_same_content(self, store) -> None:
        ref = store.put("knowledge:acme", "hello world")
        assert isinstance(ref, KnowledgeRef)
        assert store.get("knowledge:acme") == "hello world"

    def test_get_missing_returns_empty_string(self, store) -> None:
        # Contract: not-found is "" not None.
        assert store.get("knowledge:never-written") == ""

    def test_overwrite_replaces_content(self, store) -> None:
        store.put("knowledge:acme", "first")
        store.put("knowledge:acme", "second")
        assert store.get("knowledge:acme") == "second"

    def test_unicode_roundtrip(self, store) -> None:
        body = "ñ日本語🎉"
        store.put("knowledge:i18n", body)
        assert store.get("knowledge:i18n") == body


class TestList:
    def test_list_returns_refs_for_stored_blobs(self, store) -> None:
        store.put("knowledge:a", "x")
        store.put("knowledge:b", "y")
        names = {r.name for r in store.list()}
        assert {"knowledge:a", "knowledge:b"}.issubset(names)

    def test_list_prefix_filter_disambiguates_substrings(self, store) -> None:
        # Catches: `LIKE 'knowledge:%'` vs `startswith` consistency.
        store.put("knowledge:acme", "x")
        store.put("knowledge:acme2", "y")  # NOT a child of "acme"
        store.put("lessons:python", "z")
        refs = store.list(prefix="knowledge:acme")
        names = {r.name for r in refs}
        assert "knowledge:acme" in names
        assert "knowledge:acme2" in names  # both share prefix "knowledge:acme"
        assert "lessons:python" not in names

    def test_list_prefix_excludes_other_categories(self, store) -> None:
        store.put("knowledge:a", "x")
        store.put("lessons:y", "y")
        refs = store.list(prefix="knowledge:")
        names = {r.name for r in refs}
        assert names == {"knowledge:a"}

    def test_list_empty_when_nothing_stored(self, store) -> None:
        assert store.list() == []


class TestDelete:
    def test_delete_existing_returns_true(self, store) -> None:
        store.put("knowledge:a", "x")
        assert store.delete("knowledge:a") is True
        assert store.get("knowledge:a") == ""

    def test_delete_missing_returns_false(self, store) -> None:
        assert store.delete("knowledge:never") is False


class TestFingerprint:
    def test_fingerprint_matches_md5(self, store) -> None:
        content = "hello"
        store.put("knowledge:a", content)
        expected = hashlib.md5(content.encode("utf-8")).hexdigest()
        assert store.fingerprint("knowledge:a") == expected

    def test_fingerprint_missing_returns_empty(self, store) -> None:
        assert store.fingerprint("knowledge:never") == ""

    def test_fingerprint_changes_with_content(self, store) -> None:
        store.put("knowledge:a", "first")
        h1 = store.fingerprint("knowledge:a")
        store.put("knowledge:a", "second")
        h2 = store.fingerprint("knowledge:a")
        assert h1 != h2


class TestPutIfChanged:
    def test_unchanged_content_skips_write(self, store) -> None:
        store.put("knowledge:a", "same")
        result = store.put_if_changed("knowledge:a", "same")
        assert result.wrote is False
        assert result.new_hash == result.prev_hash

    def test_changed_content_writes_and_returns_ref(self, store) -> None:
        store.put("knowledge:a", "old")
        result = store.put_if_changed("knowledge:a", "new")
        assert result.wrote is True
        assert result.new_hash != result.prev_hash
        assert result.ref is not None
        assert store.get("knowledge:a") == "new"

    def test_first_write_when_missing(self, store) -> None:
        result = store.put_if_changed("knowledge:fresh", "body")
        assert result.wrote is True
        assert result.prev_hash == ""


class TestFileSpecific:
    """StoreFile path-shape semantics — only verified against file."""

    def test_blob_with_colon_routes_to_category_subdir(self, file_store, tmp_root) -> None:
        file_store.put("cat:rest", "x")
        assert (tmp_root / "knowledge" / "cat" / "rest.md").exists()

    def test_bare_name_no_colon_at_root(self, file_store, tmp_root) -> None:
        file_store.put("standalone", "x")
        assert (tmp_root / "knowledge" / "standalone.md").exists()
