"""Behaviour tests for the release-cadence extractor.

`ExtractReleaseCadence` walks `provider.list_releases` and derives the
shipping rhythm: the release count, the most-recent release, the median
number of days between consecutive releases, and the prerelease count.

The provider is mocked at the seam the composer calls — the
`RepositoryProvider.list_releases` verb — using a hand-rolled
`RepositoryProvider` subclass, the same pattern `test_code_hotspots.py`
uses. The class is imported directly rather than via the registry.
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import Release, RepositoryProvider
from briar.extract.release_cadence import ExtractReleaseCadence


def _args(**over):
    base = dict(
        release_repo=["o/r"],
        release_max=100,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _ReleaseProvider(RepositoryProvider):
    """Minimal provider that only implements the verbs the cadence
    composer touches; all other abstract verbs return inert values so
    the ABC can be instantiated."""

    kind = "fake"

    def __init__(self, releases=None, *, company: str = "") -> None:
        self._company = company
        self._releases = releases or []

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

    def list_releases(self, repo, *, max_count=100):
        return list(self._releases)


def _run(provider, args):
    ext = ExtractReleaseCadence()
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_happy_path_counts_recency_and_median_gap():
    # Three releases spaced 10 and 20 days apart → gaps [20, 10],
    # median 15.0. Provider returns them out of order to prove the
    # extractor sorts by created_at descending itself.
    releases = [
        Release("v1.0.0", "1.0.0", "2026-01-01T00:00:00Z"),
        Release("v1.2.0", "1.2.0", "2026-01-31T00:00:00Z"),  # most recent
        Release("v1.1.0", "1.1.0", "2026-01-11T00:00:00Z", is_prerelease=True),
    ]
    section = _run(_ReleaseProvider(releases), _args())

    assert section.title == "Release cadence — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"

    data = repo.data
    assert data["release_count"] == 3
    # Most-recent release wins regardless of input order.
    assert data["last_release"] == {"tag": "v1.2.0", "created_at": "2026-01-31T00:00:00Z"}
    # Gaps between 31st→11th (20d) and 11th→1st (10d) → median 15.0.
    assert data["median_days_between"] == 15.0
    assert data["prerelease_count"] == 1

    assert "releases sampled: **3**" in repo.body
    assert "latest: `v1.2.0`" in repo.body
    assert "median days between releases: **15.0**" in repo.body
    assert "prereleases: **1**" in repo.body


@pytest.mark.unit
def test_unparseable_and_empty_dates_are_skipped_from_gap_math():
    # Two valid dates 10 days apart plus an empty and a malformed date.
    # Gap math runs on the two parseable ones → median 10.0; the bad
    # ones do not crash the section. The malformed date ("2026-13...")
    # is non-empty so it sorts lexicographically but never parses.
    releases = [
        Release("v2.0.0", "2.0.0", "2026-03-11T00:00:00Z"),
        Release("v1.9.0", "1.9.0", "2026-03-01T00:00:00Z"),
        Release("v1.8.0", "1.8.0", ""),
        Release("v1.7.0", "1.7.0", "2026-13-99T00:00:00Z"),
    ]
    section = _run(_ReleaseProvider(releases), _args())
    data = section.subsections[0].data
    assert data["release_count"] == 4
    assert data["median_days_between"] == 10.0
    # The empty-date release must not float to the top of the ordering.
    assert data["last_release"]["created_at"] != ""


@pytest.mark.unit
def test_fewer_than_two_valid_dates_yields_none_median():
    # Only one parseable date → no gaps → median is None, section still
    # renders (count + last_release are computable).
    releases = [
        Release("v1.0.0", "1.0.0", "2026-01-01T00:00:00Z"),
        Release("v0.9.0", "0.9.0", ""),
    ]
    section = _run(_ReleaseProvider(releases), _args())
    repo = section.subsections[0]
    assert repo.data["median_days_between"] is None
    # The None median line must be skipped entirely.
    assert "median days between" not in repo.body


@pytest.mark.unit
def test_empty_upstream_yields_empty_section():
    section = _run(_ReleaseProvider([]), _args())
    assert section.is_empty
    assert section.title == ""
