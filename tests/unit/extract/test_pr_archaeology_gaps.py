"""Gap-filling tests for the pr-archaeology compose layer.

`tests/test_extract.py::ExtractPrArchaeologyTests` already pins the
happy path. This file covers the degradation + branch paths it skips:
empty upstream, all-repos-empty, None-median (unparsable timestamps),
the reviewers branch, and provider-error propagation.

Provider mocked at the `list_pulls` seam via a hand-rolled
`RepositoryProvider` subclass. `PullRequest` fixtures model the
GitHub shape the provider normalises, see
https://docs.github.com/en/rest/pulls/pulls#list-pull-requests
(merged PRs surface a `merged_at`; the provider maps `user.login` →
`author` and `requested_reviewers[].login` → `requested_reviewers`).
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract import EXTRACTORS
from briar.extract._provider import PullRequest, RepositoryProvider


def _pr(number, author="alice", *, created="2026-06-01T00:00:00Z", merged="2026-06-01T05:00:00Z", reviewers=()):
    return PullRequest(
        number=number,
        title=f"PR {number}",
        author=author,
        is_draft=False,
        head_ref="f",
        base_ref="main",
        review_comment_count=0,
        created_at=created,
        merged_at=merged,
        requested_reviewers=list(reviewers),
    )


class _PrProvider(RepositoryProvider):
    kind = "fake"

    def __init__(self, pulls=None, *, company="", raises=None):
        self._company = company
        self._pulls = pulls or []
        self._raises = raises

    def is_available(self):
        return True

    def resolve_token(self):
        return "t"

    def clone_url(self, owner, repo):
        return ""

    def authed_clone_url(self, owner, repo, token):
        return ""

    def pr_creation_recipe(self, *, owner, repo, branch):
        return ""

    def list_pulls(self, repo, *, state, max_count):
        self._state = state
        if self._raises:
            raise self._raises
        return list(self._pulls)

    def read_file(self, repo, path):
        return ""


def _args(repos=("o/r",)):
    return argparse.Namespace(
        pr_repo=list(repos),
        pr_max=100,
        provider="fake",
        company="",
        pr_include_users=[],
        pr_exclude_users=[],
    )


def _run(provider, args):
    ext = EXTRACTORS["pr-archaeology"]
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_empty_upstream_yields_empty_section():
    section = _run(_PrProvider([]), _args())
    assert section.is_empty


@pytest.mark.unit
def test_unparsable_merge_times_yield_none_median_but_section_present():
    section = _run(_PrProvider([_pr(1, created="bad", merged="bad")]), _args())
    repo = section.subsections[0]
    assert repo.data["median_cycle_hours"] is None
    assert "median time-to-merge" not in repo.body
    assert "merged PR sample: **1**" in repo.body


@pytest.mark.unit
def test_reviewers_surface_in_data_and_body():
    section = _run(_PrProvider([_pr(1, reviewers=["carol", "dave"]), _pr(2, reviewers=["carol"])]), _args())
    repo = section.subsections[0]
    assert repo.data["top_reviewers"][0] == ("carol", 2)
    assert "requested reviewers: carol(2)" in repo.body
    # median over two 5h PRs is 5.0
    assert repo.data["median_cycle_hours"] == 5.0
    assert "median time-to-merge: **5.0h**" in repo.body


@pytest.mark.unit
def test_state_passed_as_merged():
    prov = _PrProvider([_pr(1)])
    _run(prov, _args())
    assert prov._state == "merged"


@pytest.mark.unit
def test_provider_error_propagates():
    with pytest.raises(RuntimeError, match="500"):
        _run(_PrProvider(raises=RuntimeError("500 server error")), _args())
