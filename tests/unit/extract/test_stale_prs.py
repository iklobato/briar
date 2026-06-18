"""Behaviour tests for the stale-PRs extractor.

`ExtractStalePrs` lists open PRs and flags the ones whose age (measured
from `created_at` to *now*) exceeds the staleness threshold. Because the
age is computed against `datetime.now`, the fixtures pin determinism by
dating stale PRs far in the past (always older than the threshold) and
the not-stale PR far in the FUTURE (negative age → excluded).

The provider is hand-rolled, implementing `list_pulls(state="open")` and
returning `PullRequest` objects; we patch the composer's `_provider`
seam, the same pattern `test_code_hotspots.py` uses.
"""

from __future__ import annotations

import argparse
from typing import List

import pytest

from briar.extract._provider import PullRequest
from briar.extract.stale_prs import ExtractStalePrs

pytestmark = pytest.mark.unit

_PAST = "2020-01-01T00:00:00Z"  # always older than any threshold
_VERY_PAST = "2019-01-01T00:00:00Z"  # even older — sorts first by age desc
_FUTURE = "2099-01-01T00:00:00Z"  # negative age → never stale


def _pr(number: int, *, created_at: str, author: str = "alice", title: str = "fix", draft: bool = False) -> PullRequest:
    return PullRequest(
        number=number,
        title=title,
        author=author,
        is_draft=draft,
        head_ref="feature",
        base_ref="main",
        review_comment_count=0,
        created_at=created_at,
    )


def _args(**over) -> argparse.Namespace:
    base = dict(
        stale_repo=["o/r"],
        stale_max=100,
        stale_days=14,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


class _FakeRepoProvider:
    def __init__(self, prs: List[PullRequest]) -> None:
        self._prs = prs
        self.calls: list = []

    def is_available(self) -> bool:
        return True

    def list_pulls(self, repo: str, *, state: str, max_count: int) -> List[PullRequest]:
        self.calls.append((repo, state, max_count))
        return list(self._prs)


def _run(provider, args):
    ext = ExtractStalePrs()
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


def test_old_prs_flagged_stale_with_count_and_ordering() -> None:
    provider = _FakeRepoProvider([_pr(1, created_at=_PAST), _pr(2, created_at=_VERY_PAST)])
    section = _run(provider, _args())

    assert section.title == "Stale PRs — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r — 2 stale PR(s)"
    assert repo.data["open_pr_count"] == 2
    assert repo.data["stale_pr_count"] == 2
    assert repo.data["stale_threshold_days"] == 14

    rows = repo.data["stale_prs"]
    # Sorted by age descending: PR #2 (created 2019) older than #1 (2020).
    assert [r["number"] for r in rows] == [2, 1]
    assert rows[0]["age_days"] > rows[1]["age_days"]
    # The provider is asked for OPEN PRs with the configured cap.
    assert provider.calls == [("o/r", "open", 100)]
    assert "open " in repo.body


def test_future_dated_pr_not_counted_stale() -> None:
    provider = _FakeRepoProvider([_pr(1, created_at=_PAST), _pr(2, created_at=_FUTURE)])
    section = _run(provider, _args())

    repo = section.subsections[0]
    # Both PRs are open, but only the past-dated one is stale.
    assert repo.data["open_pr_count"] == 2
    assert repo.data["stale_pr_count"] == 1
    assert [r["number"] for r in repo.data["stale_prs"]] == [1]


def test_no_open_prs_yields_empty_section() -> None:
    section = _run(_FakeRepoProvider([]), _args())
    assert section.is_empty
    assert section.title == ""
