"""Behaviour tests for the test-discipline compose layer.

`ExtractTestDiscipline` walks `provider.list_tree`, partitions files
into TEST vs SOURCE, and reports the ratio plus the source files that
have no obvious matching test (best-effort stem substring match).

The provider is mocked at the seam the composer calls — the
`RepositoryProvider.list_tree` verb — using a hand-rolled
`RepositoryProvider` subclass, the same pattern `test_code_hotspots.py`
uses. `TreeEntry` models the file tree a real GitHub provider derives
from the git tree API (each blob/tree element flattened into a path +
`is_file`).
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import RepositoryProvider, TreeEntry
from briar.extract.test_discipline import ExtractTestDiscipline


def _args(**over):
    base = dict(
        testdisc_repo=["o/r"],
        testdisc_top_n=10,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _TreeProvider(RepositoryProvider):
    """Minimal provider that only implements `list_tree`; all other
    abstract verbs return inert values so the ABC can be instantiated."""

    kind = "fake"

    def __init__(self, entries=None, *, company: str = "") -> None:
        self._company = company
        self._entries = entries or []

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

    def list_tree(self, repo, *, max_count=5000):
        return list(self._entries)


def _run(provider, args):
    ext = ExtractTestDiscipline()
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_counts_ratio_and_flags_untested_source():
    # 3 source files (a.py, b.py, lonely.py), 2 test files.
    # a.py is covered (test_a.py), b.py is covered (b_test.py),
    # lonely.py has no matching test → must surface.
    entries = [
        TreeEntry("src/a.py"),
        TreeEntry("src/b.py"),
        TreeEntry("src/lonely.py"),
        TreeEntry("tests/test_a.py"),
        TreeEntry("src/b_test.py"),
    ]
    section = _run(_TreeProvider(entries), _args())

    assert section.title == "Test discipline — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"
    assert repo.data["source_count"] == 3
    assert repo.data["test_count"] == 2
    assert repo.data["test_ratio"] == round(2 / 3, 2)

    untested = repo.data["untested_sample"]
    assert "src/lonely.py" in untested
    assert "src/a.py" not in untested
    assert "src/b.py" not in untested
    # Body carries the mapping, not just the title.
    assert f"3 source / 2 test files (ratio {round(2 / 3, 2)})" in repo.body
    assert "`src/lonely.py`" in repo.body


@pytest.mark.unit
def test_non_code_files_excluded_from_counts():
    # README.md / config.json / a dir entry are neither source nor test.
    entries = [
        TreeEntry("app.py"),
        TreeEntry("README.md"),
        TreeEntry("config.json"),
        TreeEntry("src", is_file=False),
        TreeEntry("tests/test_app.py"),
    ]
    section = _run(_TreeProvider(entries), _args())
    repo = section.subsections[0]
    assert repo.data["source_count"] == 1  # only app.py
    assert repo.data["test_count"] == 1  # only tests/test_app.py
    # app.py is covered by test_app.py → nothing untested.
    assert repo.data["untested_sample"] == []


@pytest.mark.unit
def test_no_source_files_yields_empty_section():
    # A repo with only test files (and non-code) has no source → the
    # whole extractor returns the empty sentinel.
    entries = [
        TreeEntry("tests/test_a.py"),
        TreeEntry("spec/thing.spec.ts"),
        TreeEntry("README.md"),
    ]
    section = _run(_TreeProvider(entries), _args())
    assert section.is_empty
    assert section.title == ""


@pytest.mark.unit
def test_top_n_truncates_untested_sample():
    entries = [
        TreeEntry("a.py"),
        TreeEntry("b.py"),
        TreeEntry("c.py"),
    ]
    section = _run(_TreeProvider(entries), _args(testdisc_top_n=2))
    repo = section.subsections[0]
    assert repo.data["source_count"] == 3
    assert len(repo.data["untested_sample"]) == 2
