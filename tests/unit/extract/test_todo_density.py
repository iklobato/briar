"""Behaviour tests for the todo-density compose layer.

`ExtractTodoDensity` runs one capped code search per repo
(``"TODO OR FIXME OR HACK"``) and shapes the resulting
`CodeSearchHit` list into a density section: total markers, how many
files carry them, and the marker-heaviest files.

The provider is mocked at the seam the composer calls — the
`RepositoryProvider.search_code` verb — using a hand-rolled
`RepositoryProvider` subclass with inert stubs for the other abstract
verbs, the same pattern `test_code_hotspots.py` uses. No network.
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import CodeSearchHit, RepositoryProvider
from briar.extract.todo_density import ExtractTodoDensity


def _args(**over):
    base = dict(
        todo_repo=["o/r"],
        todo_max=200,
        todo_top_n=10,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _SearchProvider(RepositoryProvider):
    """Minimal provider that only implements the verbs the todo-density
    composer touches; all other abstract verbs return inert values so
    the ABC can be instantiated."""

    kind = "fake"

    def __init__(self, hits=None, *, company: str = "") -> None:
        self._company = company
        self._hits = hits or []
        self.queries: list[str] = []

    def is_available(self) -> bool:
        return True

    def resolve_token(self) -> str:
        return "fake-token"

    def clone_url(self, owner, repo):
        return f"https://fake/{owner}/{repo}.git"

    def authed_clone_url(self, owner, repo, token):
        return f"https://x:{token}@fake/{owner}/{repo}.git"

    def pr_creation_recipe(self, *, owner, repo, branch):
        return ""

    def list_pulls(self, repo, *, state, max_count):
        return []

    def read_file(self, repo, path):
        return ""

    def search_code(self, repo, query, *, max_count=200):
        self.queries.append(query)
        return list(self._hits)


def _run(provider, args):
    ext = ExtractTodoDensity()
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_totals_counts_and_ranking():
    provider = _SearchProvider(
        [
            CodeSearchHit("a.py", 5),
            CodeSearchHit("b.py", 2),
            CodeSearchHit("c.py", 9),
        ]
    )
    section = _run(provider, _args())

    assert section.title == "TODO/FIXME density — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"
    # total_markers is the sum of per-file match counts.
    assert repo.data["total_markers"] == 16
    assert repo.data["files_with_markers"] == 3
    # top_files ordered by matches desc — a flipped comparator reorders.
    assert [f["file_path"] for f in repo.data["top_files"]] == ["c.py", "a.py", "b.py"]
    assert [f["matches"] for f in repo.data["top_files"]] == [9, 5, 2]
    # body carries the summary line + a bullet per top file.
    assert "16 markers across 3 file(s)." in repo.body
    assert "`c.py` (9)" in repo.body
    # The expected code-search query reached the provider.
    assert provider.queries == ["TODO OR FIXME OR HACK"]


@pytest.mark.unit
def test_top_n_truncates_ranked_files():
    provider = _SearchProvider(
        [
            CodeSearchHit("f1.py", 1),
            CodeSearchHit("f2.py", 3),
            CodeSearchHit("f3.py", 2),
        ]
    )
    section = _run(provider, _args(todo_top_n=2))
    repo = section.subsections[0]
    # top-2 by matches keeps f2(3), f3(2); drops f1(1).
    assert [f["file_path"] for f in repo.data["top_files"]] == ["f2.py", "f3.py"]
    # Truncation does NOT change the full-population totals.
    assert repo.data["total_markers"] == 6
    assert repo.data["files_with_markers"] == 3


@pytest.mark.unit
def test_empty_upstream_yields_empty_section():
    section = _run(_SearchProvider([]), _args())
    assert section.is_empty
    assert section.title == ""
