"""Behaviour tests for the reviewer-profile compose layer.

`ExtractReviewerProfile` samples recent merged PRs, then for each PR
walks `list_pr_comments` to build per-reviewer aggregates: total
comments, PRs-reviewed count, avg comments/PR, hot files, and a short
sample of comment bodies. It excludes self-comments (author == PR
author) and ranks reviewers by total comment volume.

Provider mocked at the `list_pulls` + `list_pr_comments` seams.
`PullRequest` / `ReviewComment` fixtures model the GitHub shapes the
provider normalises:
  - PR list:        https://docs.github.com/en/rest/pulls/pulls#list-pull-requests
  - review comments: https://docs.github.com/en/rest/pulls/comments#list-review-comments-on-a-pull-request
                     (each has `user.login`, `body`, `path`, `line`)
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract import EXTRACTORS
from briar.extract._provider import PullRequest, RepositoryProvider, ReviewComment


def _pr(number, author):
    return PullRequest(
        number=number,
        title=f"PR {number}",
        author=author,
        is_draft=False,
        head_ref="f",
        base_ref="main",
        review_comment_count=0,
        created_at="2026-06-01T00:00:00Z",
        merged_at="2026-06-02T00:00:00Z",
    )


def _rc(author, body, file_path=""):
    return ReviewComment(id="c", author=author, body=body, file_path=file_path)


class _ReviewProvider(RepositoryProvider):
    kind = "fake"

    def __init__(self, *, pulls=None, comments_by_pr=None, company="", raises=None):
        self._company = company
        self._pulls = pulls or []
        # map {pr_number: [ReviewComment, ...]}
        self._comments = comments_by_pr or {}
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
        self._pull_state = state
        self._pull_max = max_count
        return list(self._pulls)

    def read_file(self, repo, path):
        return ""

    def list_pr_comments(self, repo, number):
        if self._raises:
            raise self._raises
        return list(self._comments.get(number, []))


def _args(**over):
    base = dict(
        reviewer_repo=["o/r"],
        reviewer_pr_sample=20,
        reviewer_top_n=5,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


def _run(provider, args):
    ext = EXTRACTORS["reviewer-profile"]
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_compose_aggregates_per_reviewer_and_ranks():
    # PR 1 by alice: bob leaves 2 comments, carol 1. PR 2 by alice: bob 1.
    # bob: 3 comments / 2 PRs ; carol: 1 comment / 1 PR.
    pulls = [_pr(1, "alice"), _pr(2, "alice")]
    comments = {
        1: [
            _rc("bob", "please rename this function for clarity here", "src/a.py"),
            _rc("bob", "extract this into a helper to reduce nesting", "src/a.py"),
            _rc("carol", "nit: trailing whitespace on this line here", "src/b.py"),
        ],
        2: [_rc("bob", "add a regression test for this edge case path", "src/a.py")],
    }
    section = _run(_ReviewProvider(pulls=pulls, comments_by_pr=comments), _args())

    assert section.title == "Reviewer profiles — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"
    assert repo.data["pr_sample_size"] == 2

    rows = repo.data["reviewers"]
    # Ranked by total comments: bob(3) before carol(1).
    assert [r["reviewer"] for r in rows] == ["bob", "carol"]

    bob = rows[0]
    assert bob["comments"] == 3
    assert bob["prs_reviewed"] == 2
    assert bob["avg_comments_per_pr"] == 1.5  # 3 / 2
    assert bob["top_files"] == ["src/a.py"]  # all 3 of bob's comments on a.py

    carol = rows[1]
    assert carol["comments"] == 1
    assert carol["prs_reviewed"] == 1
    assert carol["avg_comments_per_pr"] == 1.0

    # Body reflects mined values, including the sample-ask block.
    assert "Sample: 2 merged PRs" in repo.body
    assert "Active reviewers: 2" in repo.body
    assert "### bob" in repo.body
    assert "PRs reviewed: **2** / comments left: **3** (avg **1.5**/PR)" in repo.body
    assert "Hot files: src/a.py" in repo.body
    assert "please rename this function for clarity here" in repo.body


@pytest.mark.unit
def test_self_comments_are_excluded():
    # alice (the PR author) comments on her own PR — must not count.
    pulls = [_pr(1, "alice")]
    comments = {1: [_rc("alice", "self note ignored please skip me here"), _rc("bob", "real review comment with enough length")]}
    repo = _run(_ReviewProvider(pulls=pulls, comments_by_pr=comments), _args()).subsections[0]
    reviewers = {r["reviewer"] for r in repo.data["reviewers"]}
    assert reviewers == {"bob"}
    assert "Active reviewers: 1" in repo.body


@pytest.mark.unit
def test_short_comments_not_sampled_but_still_counted():
    # Bodies <= 20 chars are counted toward totals but never sampled.
    pulls = [_pr(1, "alice")]
    comments = {1: [_rc("bob", "lgtm"), _rc("bob", "ok")]}
    repo = _run(_ReviewProvider(pulls=pulls, comments_by_pr=comments), _args()).subsections[0]
    bob = repo.data["reviewers"][0]
    assert bob["comments"] == 2
    # No "Sample asks" block because both bodies were too short.
    assert "Sample asks" not in repo.body


@pytest.mark.unit
def test_top_n_limits_profiled_reviewers():
    pulls = [_pr(1, "author")]
    # 3 reviewers with distinct volumes: r3=3, r2=2, r1=1.
    comments = {
        1: [_rc("r3", "comment body that is definitely long enough one")] * 3
        + [_rc("r2", "comment body that is definitely long enough two")] * 2
        + [_rc("r1", "comment body that is definitely long enough three")]
    }
    repo = _run(_ReviewProvider(pulls=pulls, comments_by_pr=comments), _args(reviewer_top_n=2)).subsections[0]
    assert [r["reviewer"] for r in repo.data["reviewers"]] == ["r3", "r2"]


@pytest.mark.unit
def test_pr_sample_state_and_count_passed_to_provider():
    prov = _ReviewProvider(pulls=[_pr(1, "a")], comments_by_pr={1: [_rc("b", "long enough comment body to count")]})
    _run(prov, _args(reviewer_pr_sample=7)).subsections  # noqa: B018
    assert prov._pull_state == "merged"
    assert prov._pull_max == 7


@pytest.mark.unit
def test_no_merged_prs_yields_empty_section():
    section = _run(_ReviewProvider(pulls=[]), _args())
    assert section.is_empty


@pytest.mark.unit
def test_prs_with_no_external_comments_yields_empty_section():
    # PRs exist but every comment is a self-comment → no reviewer data.
    pulls = [_pr(1, "alice")]
    comments = {1: [_rc("alice", "only self comments here so nothing counts")]}
    section = _run(_ReviewProvider(pulls=pulls, comments_by_pr=comments), _args())
    assert section.is_empty


@pytest.mark.unit
def test_provider_error_propagates():
    pulls = [_pr(1, "alice")]
    with pytest.raises(RuntimeError, match="429"):
        _run(_ReviewProvider(pulls=pulls, raises=RuntimeError("429 rate limit")), _args())
