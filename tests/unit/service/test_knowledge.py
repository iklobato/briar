"""Knowledge service — reads, and gated writes that no-op on DRY_RUN."""

from __future__ import annotations

from briar.service import GateMode
from briar.service import knowledge as ks


def _root(tmp_path):
    return str(tmp_path / "knowledge")


def test_put_execute_writes_and_get_reads_back(tmp_path) -> None:
    out = ks.put_blob(blob_name="knowledge:acme", content="hello", root=_root(tmp_path))
    assert out.executed is True
    assert out.result["ref"]["name"] == "knowledge:acme"
    assert ks.get_blob(blob_name="knowledge:acme", root=_root(tmp_path)) == "hello"


def test_put_dry_run_does_not_write(tmp_path) -> None:
    out = ks.put_blob(blob_name="knowledge:acme", content="hello", root=_root(tmp_path), gate=GateMode.DRY_RUN)
    assert out.executed is False
    assert "would write" in out.summary
    # The side effect did NOT happen.
    assert ks.get_blob(blob_name="knowledge:acme", root=_root(tmp_path)) is None


def test_get_missing_returns_none(tmp_path) -> None:
    assert ks.get_blob(blob_name="knowledge:nope", root=_root(tmp_path)) is None


def test_list_and_categories(tmp_path) -> None:
    root = _root(tmp_path)
    ks.put_blob(blob_name="knowledge:a", content="x", root=root)
    ks.put_blob(blob_name="lessons:b", content="y", root=root)
    names = {b["name"] for b in ks.list_blobs(root=root)}
    assert names == {"knowledge:a", "lessons:b"}
    assert {b["name"] for b in ks.list_blobs(root=root, prefix="knowledge:")} == {"knowledge:a"}
    cats = {c["category"]: c["blob_count"] for c in ks.categories(root=root)}
    assert cats == {"knowledge": 1, "lessons": 1}


def test_delete_dry_run_keeps_blob(tmp_path) -> None:
    root = _root(tmp_path)
    ks.put_blob(blob_name="knowledge:a", content="x", root=root)
    out = ks.delete_blob(blob_name="knowledge:a", root=root, gate=GateMode.DRY_RUN)
    assert out.executed is False
    assert "would delete" in out.summary and "exists" in out.summary
    assert ks.get_blob(blob_name="knowledge:a", root=root) == "x"


def test_delete_execute_removes_blob(tmp_path) -> None:
    root = _root(tmp_path)
    ks.put_blob(blob_name="knowledge:a", content="x", root=root)
    out = ks.delete_blob(blob_name="knowledge:a", root=root)
    assert out.executed is True
    assert out.result["removed"] is True
    assert ks.get_blob(blob_name="knowledge:a", root=root) is None
