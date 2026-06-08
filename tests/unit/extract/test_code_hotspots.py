"""Behaviour tests for the code-hotspots compose layer.

`ExtractCodeHotspots` walks `provider.list_recent_commits` and builds a
co-change matrix: per file, how often it appears in the same commit as
another. It ranks files by total commit involvement (touch count) and
surfaces each file's top co-changing partners.

The provider is mocked at the seam the composer calls — the
`RepositoryProvider.list_recent_commits` verb — using a hand-rolled
`RepositoryProvider` subclass, the same pattern `test_extract.py`'s
`FakeProvider` uses. `Commit.file_paths` models the file list a real
GitHub provider derives from the commit "files" array, see
https://docs.github.com/en/rest/commits/commits#get-a-commit
(each element has a `filename`; the provider flattens these into
`file_paths`).
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract import EXTRACTORS
from briar.extract._provider import Commit, RepositoryProvider


def _args(**over):
    base = dict(
        hotspots_repo=["o/r"],
        hotspots_since_days=30,
        hotspots_max_commits=100,
        hotspots_top_n=10,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _CommitProvider(RepositoryProvider):
    """Minimal provider that only implements the verbs the hotspots
    composer touches; all other abstract verbs return inert values so
    the ABC can be instantiated."""

    kind = "fake"

    def __init__(self, commits=None, *, company: str = "", raises: Exception | None = None) -> None:
        self._company = company
        self._commits = commits or []
        self._raises = raises

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

    def list_recent_commits(self, repo, *, since_days=30, max_count=200):
        if self._raises is not None:
            raise self._raises
        return list(self._commits)


def _run(provider, args):
    ext = EXTRACTORS["code-hotspots"]
    # Patch the composer's _provider seam to return our fake.
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_compose_ranks_files_by_touch_count_and_lists_co_changers():
    # 3 commits. churn: a.py in 3 commits, test_a.py in 2, b.py in 1.
    # Expected ranking by touch count: a.py(3) > test_a.py(2) > b.py(1).
    commits = [
        Commit("s1", "alice", "m1", "2026-06-01T00:00:00Z", ["a.py", "test_a.py"]),
        Commit("s2", "bob", "m2", "2026-06-02T00:00:00Z", ["a.py", "test_a.py", "b.py"]),
        Commit("s3", "alice", "m3", "2026-06-03T00:00:00Z", ["a.py"]),
    ]
    section = _run(_CommitProvider(commits), _args())

    assert section.title == "Code hotspots — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"
    assert repo.data["commit_sample_size"] == 3

    hotspots = repo.data["hotspots"]
    # Ranking order is load-bearing: a flipped comparator would reorder.
    assert [h["path"] for h in hotspots] == ["a.py", "test_a.py", "b.py"]
    assert [h["touches"] for h in hotspots] == [3, 2, 1]

    # a.py and test_a.py co-changed twice (>1 → surfaced); b.py once each (==1 → dropped).
    a_co = {c["path"]: c["count"] for c in hotspots[0]["top_co_changers"]}
    assert a_co == {"test_a.py": 2}
    # The body must carry the mapping, not just the title.
    assert "`a.py` (touched 3×)" in repo.body
    assert "co-changes with: `test_a.py` (2)" in repo.body
    assert "Sample: 3 commits over 30 days" in repo.body


@pytest.mark.unit
def test_top_n_truncates_ranked_files():
    commits = [
        Commit("s1", "a", "m", "2026-06-01T00:00:00Z", ["f1.py"]),
        Commit("s2", "a", "m", "2026-06-01T00:00:00Z", ["f1.py", "f2.py"]),
        Commit("s3", "a", "m", "2026-06-01T00:00:00Z", ["f1.py", "f2.py", "f3.py"]),
    ]
    section = _run(_CommitProvider(commits), _args(hotspots_top_n=2))
    hotspots = section.subsections[0].data["hotspots"]
    # touches: f1=3, f2=2, f3=1 → top-2 keeps f1, f2 only.
    assert [h["path"] for h in hotspots] == ["f1.py", "f2.py"]


@pytest.mark.unit
def test_duplicate_paths_in_one_commit_count_once():
    # A commit listing the same file twice must not inflate its churn.
    commits = [
        Commit("s1", "a", "m", "2026-06-01T00:00:00Z", ["dup.py", "dup.py", "other.py"]),
        Commit("s2", "a", "m", "2026-06-02T00:00:00Z", ["dup.py"]),
    ]
    section = _run(_CommitProvider(commits), _args())
    hotspots = {h["path"]: h["touches"] for h in section.subsections[0].data["hotspots"]}
    assert hotspots["dup.py"] == 2  # 2 commits, not 3 occurrences


@pytest.mark.unit
def test_commits_without_file_lists_are_filtered_out():
    # Cost-budget caps can return commits with empty file_paths — they
    # must not appear in the sample size nor the matrix.
    commits = [
        Commit("s1", "a", "m", "2026-06-01T00:00:00Z", ["real.py"]),
        Commit("s2", "a", "m", "2026-06-02T00:00:00Z", []),
    ]
    section = _run(_CommitProvider(commits), _args())
    repo = section.subsections[0]
    assert repo.data["commit_sample_size"] == 1
    assert "Sample: 1 commits" in repo.body


@pytest.mark.unit
def test_empty_upstream_yields_empty_present_section():
    # No usable commits at all → the whole extractor returns the empty
    # sentinel (title == "", composer skips it).
    section = _run(_CommitProvider([]), _args())
    assert section.is_empty
    assert section.title == ""


@pytest.mark.unit
def test_only_fileless_commits_yields_empty_section():
    commits = [Commit("s1", "a", "m", "2026-06-01T00:00:00Z", [])]
    section = _run(_CommitProvider(commits), _args())
    assert section.is_empty


@pytest.mark.unit
def test_provider_error_propagates():
    # The composer does NOT swallow provider errors (unlike aws-infra's
    # UNREACHABLE path) — list_recent_commits raising must surface.
    with pytest.raises(RuntimeError, match="rate limited"):
        _run(_CommitProvider(raises=RuntimeError("rate limited")), _args())


@pytest.mark.unit
def test_since_days_flows_into_provider_and_body():
    captured = {}

    class _Capturing(_CommitProvider):
        def list_recent_commits(self, repo, *, since_days=30, max_count=200):
            captured["since_days"] = since_days
            captured["max_count"] = max_count
            return [Commit("s1", "a", "m", "t", ["x.py"])]

    section = _run(_Capturing(), _args(hotspots_since_days=7, hotspots_max_commits=50))
    assert captured == {"since_days": 7, "max_count": 50}
    assert "over 7 days" in section.subsections[0].body
