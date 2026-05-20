"""KnowledgeStore tests — file backend on disk, briar-api on a stub client."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

from briar.storage import make_store
from briar.storage.base import category_of
from briar.storage.briar_api import StoreBriarApi
from briar.storage.file import StoreFile


class CategoryOfTests(unittest.TestCase):
    def test_prefix(self) -> None:
        self.assertEqual(category_of("knowledge:acme"), "knowledge")
        self.assertEqual(category_of("memory:reviewer-iklobato"), "memory")

    def test_no_colon_no_category(self) -> None:
        self.assertEqual(category_of("plain"), "")


class StoreFileTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = StoreFile(Path(td))
            ref = store.put("knowledge:acme", "hello world", category="knowledge")
            self.assertEqual(ref.byte_count, len("hello world"))
            self.assertEqual(store.get("knowledge:acme"), "hello world")
            self.assertIsNone(store.get("nothing:here"))

    def test_list_with_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = StoreFile(Path(td))
            store.put("knowledge:a", "x")
            store.put("knowledge:b", "y")
            store.put("memory:c", "z")
            knowledge_only = store.list(prefix="knowledge:")
            self.assertEqual({r.name for r in knowledge_only},
                             {"knowledge:a", "knowledge:b"})

    def test_delete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = StoreFile(Path(td))
            store.put("knowledge:a", "x")
            self.assertTrue(store.delete("knowledge:a"))
            self.assertFalse(store.delete("knowledge:a"))  # second time → False

    def test_path_layout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = StoreFile(Path(td))
            ref = store.put("knowledge:acme", "x")
            self.assertEqual(
                Path(ref.extra["path"]),
                Path(td) / "knowledge" / "acme.md",
            )


class _StubClient:
    """Minimal ApiClient shim for the briar-api store tests."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.rows: Dict[str, Dict[str, Any]] = {}
        self._counter = 0

    def list_all(
        self,
        path: str,
        query: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if path != "/api/v1/sources/":
            return []
        return list(self.rows.values())

    def request(
        self,
        method: str,
        path: str,
        body: Optional[Any] = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> Any:
        self.calls.append((method, path, body))
        if method == "POST" and path == "/api/v1/sources/":
            self._counter += 1
            row_id = f"uuid-{self._counter}"
            row = {"id": row_id, **(body or {})}
            self.rows[row_id] = row
            return row
        if method == "PATCH" and path.startswith("/api/v1/sources/"):
            row_id = path.rstrip("/").rsplit("/", 1)[-1]
            self.rows[row_id] = {**self.rows[row_id], **(body or {})}
            return self.rows[row_id]
        if method == "DELETE" and path.startswith("/api/v1/sources/"):
            row_id = path.rstrip("/").rsplit("/", 1)[-1]
            self.rows.pop(row_id, None)
            return None
        return None


class StoreBriarApiTests(unittest.TestCase):
    def test_put_creates_source_row(self) -> None:
        client = _StubClient()
        store = StoreBriarApi(client)  # type: ignore[arg-type]
        ref = store.put("knowledge:acme", "hello", category="knowledge")
        self.assertEqual(ref.name, "knowledge:acme")
        post = next(c for c in client.calls if c[0] == "POST")
        self.assertEqual(post[1], "/api/v1/sources/")
        self.assertEqual(post[2]["kind"], "static")
        self.assertEqual(post[2]["config"]["content"], "hello")
        self.assertEqual(post[2]["config"]["category"], "knowledge")

    def test_put_updates_existing(self) -> None:
        client = _StubClient()
        store = StoreBriarApi(client)  # type: ignore[arg-type]
        store.put("knowledge:acme", "v1")
        store.put("knowledge:acme", "v2")  # second put → PATCH
        verbs = [c[0] for c in client.calls]
        self.assertEqual(verbs.count("POST"), 1)
        self.assertEqual(verbs.count("PATCH"), 1)

    def test_get_returns_content(self) -> None:
        client = _StubClient()
        store = StoreBriarApi(client)  # type: ignore[arg-type]
        store.put("knowledge:acme", "the body")
        self.assertEqual(store.get("knowledge:acme"), "the body")
        self.assertIsNone(store.get("not-there"))

    def test_list_with_prefix(self) -> None:
        client = _StubClient()
        store = StoreBriarApi(client)  # type: ignore[arg-type]
        store.put("knowledge:a", "x")
        store.put("knowledge:b", "y")
        store.put("memory:c", "z")
        refs = store.list(prefix="knowledge:")
        self.assertEqual({r.name for r in refs},
                         {"knowledge:a", "knowledge:b"})

    def test_delete(self) -> None:
        client = _StubClient()
        store = StoreBriarApi(client)  # type: ignore[arg-type]
        store.put("knowledge:a", "x")
        self.assertTrue(store.delete("knowledge:a"))
        self.assertFalse(store.delete("knowledge:a"))


class MakeStoreTests(unittest.TestCase):
    def test_file_backend(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = make_store("file", file_root=Path(td))
            self.assertEqual(store.name, "file")

    def test_unknown_backend_rejected(self) -> None:
        from briar.errors import CliError
        with self.assertRaises(CliError):
            make_store("bogus")


if __name__ == "__main__":
    unittest.main()
