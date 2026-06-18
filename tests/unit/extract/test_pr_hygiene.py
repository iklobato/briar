"""Behaviour tests for the pr-hygiene compose layer.

`ExtractPrHygiene` reads `provider.list_pulls` for the full merged set
(rubber-stamp rate = fraction with zero review comments) and hydrates a
capped sample via `provider.get_pull` (size distribution + large-PR
rate) plus `provider.list_pr_comments` (time-to-first-review).

The provider is mocked at the seam the composer calls — a hand-rolled
`RepositoryProvider` subclass with inert stubs for the abstract verbs it
doesn't touch, the same pattern `test_code_hotspots.py` uses. The key
asymmetry under test: `list_pulls` does NOT carry diffstat (additions /
deletions stay 0), so the composer must hydrate sizes through
`get_pull`, while `review_comment_count` IS on the list rows."""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import PullRequest, RepositoryProvider, ReviewComment
from briar.extract.pr_hygiene import ExtractPrHygiene


def _args(**over):
    base = dict(
        prhygiene_repo=["o/r"],
        prhygiene_max=100,
        prhygiene_diffstat_sample=30,
        prhygiene_large_loc=400,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


def _list_pull(number: int, *, review_comment_count: int, created_at: str) -> PullRequest:
    # Mirrors a list_pulls row: diffstat fields stay at their 0 defaults.
    return PullRequest(
        number=number,
        title=f"pr-{number}",
        author="alice",
        is_draft=False,
        head_ref="feat",
        base_ref="main",
        review_comment_count=review_comment_count,
        created_at=created_at,
    )


class _HygieneProvider(RepositoryProvider):
    """Minimal provider implementing only the verbs the hygiene composer
    touches; all other abstract verbs return inert values so the ABC can
    be instantiated.

    `merged` is the list_pulls result (no diffstat). `diffstats` maps a
    PR number to (additions, deletions) returned by get_pull. `comments`
    maps a PR number to a list of ReviewComment."""

    kind = "fake"

    def __init__(self, merged=None, diffstats=None, comments=None, *, company: str = "") -> None:
        self._company = company
        self._merged = merged or []
        self._diffstats = diffstats or {}
        self._comments = comments or {}

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

    def read_file(self, repo, path):
        return ""

    def list_pulls(self, repo, *, state, max_count):
        return list(self._merged[:max_count])

    def get_pull(self, repo, number):
        adds, dels = self._diffstats.get(number, (0, 0))
        return PullRequest(
            number=number,
            title=f"pr-{number}",
            author="alice",
            is_draft=False,
            head_ref="feat",
            base_ref="main",
            review_comment_count=0,
            created_at="",
            additions=adds,
            deletions=dels,
        )

    def list_pr_comments(self, repo, number):
        return list(self._comments.get(number, []))


def _run(provider, args):
    ext = ExtractPrHygiene()
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_happy_path_computes_size_and_review_metrics():
    # 3 merged PRs. sizes via get_pull: 100, 300, 900 LOC.
    # large_loc=400 → only pr3 (900) is large → 1/3 ≈ 0.33.
    # PR1 has 2 review comments, PR2 has 1, PR3 has 0 → rubber-stamp 1/3.
    merged = [
        _list_pull(1, review_comment_count=2, created_at="2026-06-01T00:00:00Z"),
        _list_pull(2, review_comment_count=1, created_at="2026-06-02T00:00:00Z"),
        _list_pull(3, review_comment_count=0, created_at="2026-06-03T00:00:00Z"),
    ]
    diffstats = {1: (60, 40), 2: (200, 100), 3: (500, 400)}
    comments = {
        # first review 2h after PR1 opened (earliest of the two).
        1: [
            ReviewComment(id="c1", author="bob", body="nit", created_at="2026-06-01T05:00:00Z"),
            ReviewComment(id="c0", author="carol", body="lgtm", created_at="2026-06-01T02:00:00Z"),
        ],
        # first review 4h after PR2 opened.
        2: [ReviewComment(id="c2", author="bob", body="fix", created_at="2026-06-02T04:00:00Z")],
        # no comments on PR3.
        3: [],
    }
    section = _run(_HygieneProvider(merged, diffstats, comments), _args())

    assert section.title == "PR hygiene — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"

    data = repo.data
    assert data["merged_pr_count"] == 3
    assert data["diffstat_sample_size"] == 3
    # sizes sorted: [100, 300, 900] → median 300; p90 at index int(0.9*2)=1 → 300.
    assert data["median_pr_size"] == 300
    assert data["p90_pr_size"] == 300
    assert data["large_pr_rate"] == 0.33
    assert data["rubber_stamp_rate"] == 0.33
    # first-review hours: PR1 → 2.0, PR2 → 4.0 (PR3 none) → median 3.0.
    assert data["median_hours_to_first_review"] == 3.0

    # Body carries the populated bullets.
    assert "median PR size: **300 LOC**" in repo.body
    assert "p90 PR size: **300 LOC**" in repo.body
    assert "large-PR rate (>400 LOC): **0.33**" in repo.body
    assert "rubber-stamp rate (0 review comments): **0.33**" in repo.body
    assert "median time-to-first-review: **3.0h**" in repo.body


@pytest.mark.unit
def test_rubber_stamp_rate_spans_full_merged_set_not_just_sample():
    # 4 merged PRs, but diffstat sample capped at 2. Two of the FOUR have
    # zero review comments → rubber-stamp 2/4 = 0.5. A sample-only
    # computation (first 2 PRs, both with comments) would wrongly give 0.0.
    merged = [
        _list_pull(1, review_comment_count=3, created_at="2026-06-01T00:00:00Z"),
        _list_pull(2, review_comment_count=2, created_at="2026-06-02T00:00:00Z"),
        _list_pull(3, review_comment_count=0, created_at="2026-06-03T00:00:00Z"),
        _list_pull(4, review_comment_count=0, created_at="2026-06-04T00:00:00Z"),
    ]
    diffstats = {1: (10, 0), 2: (20, 0), 3: (30, 0), 4: (40, 0)}
    section = _run(
        _HygieneProvider(merged, diffstats),
        _args(prhygiene_max=100, prhygiene_diffstat_sample=2),
    )
    data = section.subsections[0].data
    assert data["merged_pr_count"] == 4
    assert data["diffstat_sample_size"] == 2  # sample really was capped
    assert data["rubber_stamp_rate"] == 0.5  # full set, not the 2-PR sample


@pytest.mark.unit
def test_empty_upstream_yields_empty_section():
    section = _run(_HygieneProvider([]), _args())
    assert section.is_empty
    assert section.title == ""
