"""Behaviour tests for the review-nits compose layer.

`ExtractReviewNits` samples recent merged PRs, then for each PR walks
`list_pr_comments` and maps each comment body onto a fixed set of
canonical review-ask categories (missing test, naming, magic value,
…). It counts each comment at most once per category, captures the
first matching comment as an example, drops zero-count categories,
ranks the rest by count, and keeps the top N.

The class is not yet registered in `EXTRACTORS`, so the test imports
it directly and monkeypatches its `_provider` seam — same fake-provider
pattern as test_reviewer_profile / test_code_hotspots.
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract._provider import PullRequest, RepositoryProvider, ReviewComment
from briar.extract.review_nits import ExtractReviewNits


def _pr(number, author="alice"):
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
    """Minimal provider implementing only the verbs review-nits touches;
    all other abstract verbs return inert values so the ABC instantiates."""

    kind = "fake"

    def __init__(self, *, pulls=None, comments_by_pr=None, company="", raises=None):
        self._company = company
        self._pulls = pulls or []
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
        nits_repo=["o/r"],
        nits_pr_sample=30,
        nits_top_n=15,
        provider="fake",
        company="",
    )
    base.update(over)
    return argparse.Namespace(**base)


def _run(provider, args):
    ext = ExtractReviewNits()
    orig = ext._provider
    ext._provider = lambda a: provider  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._provider = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_compose_clusters_recurring_asks_with_examples():
    pulls = [_pr(1), _pr(2)]
    comments = {
        1: [
            _rc("bob", "Please add a test for the empty-input path here."),
            _rc("carol", "Use an enum here instead of these bare strings."),
        ],
        2: [
            _rc("bob", "needs a test covering the retry branch too"),
        ],
    }
    section = _run(_ReviewProvider(pulls=pulls, comments_by_pr=comments), _args())

    assert section.title == "Recurring review asks — 1 repo(s)"
    repo = section.subsections[0]
    assert repo.title == "o/r"
    assert repo.data["pr_sample_size"] == 2
    assert repo.data["comment_count"] == 3

    by_ask = {r["ask"]: r for r in repo.data["recurring_asks"]}
    assert by_ask["missing test"]["count"] == 2  # "add a test" + "needs a test"
    assert by_ask["magic value / use constant"]["count"] == 1  # "use an enum"
    # First matching comment is captured as the example.
    assert by_ask["missing test"]["example"] == "Please add a test for the empty-input path here."
    assert by_ask["magic value / use constant"]["example"] == "Use an enum here instead of these bare strings."

    # Body carries a terse bullet per ask.
    assert "**missing test** ×2" in repo.body
    assert "**magic value / use constant** ×1" in repo.body


@pytest.mark.unit
def test_more_frequent_ask_ranks_first():
    # naming appears in 3 comments; docs/comments in 1 → naming ranks first.
    pulls = [_pr(1)]
    comments = {
        1: [
            _rc("bob", "rename this variable, it is unclear"),
            _rc("bob", "this is confusing name, pick a clearer name"),
            _rc("carol", "naming nit on the helper below"),
            _rc("dave", "add a docstring explaining this branch"),
        ]
    }
    repo = _run(_ReviewProvider(pulls=pulls, comments_by_pr=comments), _args()).subsections[0]
    asks = [r["ask"] for r in repo.data["recurring_asks"]]
    assert asks[0] == "naming"
    by_ask = {r["ask"]: r["count"] for r in repo.data["recurring_asks"]}
    assert by_ask["naming"] == 3
    assert by_ask["docs / comments"] == 1
    assert asks.index("naming") < asks.index("docs / comments")


@pytest.mark.unit
def test_comment_counted_once_per_category():
    # A single comment hitting two keywords of the same category counts once.
    pulls = [_pr(1)]
    comments = {1: [_rc("bob", "rename this, the naming is confusing name overall")]}
    repo = _run(_ReviewProvider(pulls=pulls, comments_by_pr=comments), _args()).subsections[0]
    by_ask = {r["ask"]: r["count"] for r in repo.data["recurring_asks"]}
    assert by_ask["naming"] == 1


@pytest.mark.unit
def test_top_n_truncates_categories():
    pulls = [_pr(1)]
    comments = {
        1: [
            _rc("bob", "rename for clarity"),  # naming
            _rc("bob", "rename again"),  # naming (2)
            _rc("carol", "add a docstring"),  # docs (1)
        ]
    }
    repo = _run(_ReviewProvider(pulls=pulls, comments_by_pr=comments), _args(nits_top_n=1)).subsections[0]
    asks = [r["ask"] for r in repo.data["recurring_asks"]]
    assert asks == ["naming"]


@pytest.mark.unit
def test_pr_sample_state_and_count_passed_to_provider():
    prov = _ReviewProvider(pulls=[_pr(1)], comments_by_pr={1: [_rc("bob", "add a test please")]})
    _run(prov, _args(nits_pr_sample=7)).subsections  # noqa: B018
    assert prov._pull_state == "merged"
    assert prov._pull_max == 7


@pytest.mark.unit
def test_no_merged_prs_yields_empty_section():
    section = _run(_ReviewProvider(pulls=[]), _args())
    assert section.is_empty


@pytest.mark.unit
def test_no_matching_asks_yields_empty_section():
    # PRs and comments exist, but none map to a known category.
    pulls = [_pr(1)]
    comments = {1: [_rc("bob", "lgtm, shipping this")]}
    section = _run(_ReviewProvider(pulls=pulls, comments_by_pr=comments), _args())
    assert section.is_empty


@pytest.mark.unit
def test_provider_error_propagates():
    pulls = [_pr(1)]
    with pytest.raises(RuntimeError, match="429"):
        _run(_ReviewProvider(pulls=pulls, raises=RuntimeError("429 rate limit")), _args())
