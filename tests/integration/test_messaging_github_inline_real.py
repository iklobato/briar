"""End-to-end: the REAL GithubPrCommentWriter (PyGithub) posting an INLINE
review comment against a wire-level mock, so the head-SHA resolution, the
commit fetch, the review-comment POST, and the SendResult mapping all run.

(The top-level issue-comment path is exemplified in test_messaging_real.py;
this file covers the inline review-comment branch + the bare-slug target form.)

GitHub review comments:
  POST /repos/{owner}/{repo}/pulls/{pull_number}/comments
  https://docs.github.com/en/rest/pulls/comments#create-a-review-comment-for-a-pull-request
  201 Created; the response carries the new review-comment `id` (int).

PyGithub follows hypermedia for writes/reads: it builds each sub-resource URL
from the parent response's `url`, so every seeded body's `url` must point back
at the mock server.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def test_github_inline_review_comment_real(github_at) -> None:
    from briar.messaging.github_pr_comment import GithubPrCommentWriter

    base = github_at.base_url
    github_at.add("GET", "/repos/acme/app", {"id": 1, "name": "app", "full_name": "acme/app", "url": f"{base}/repos/acme/app"})
    github_at.add(
        "GET",
        "/repos/acme/app/pulls/42",
        {
            "number": 42,
            "title": "x",
            "head": {"sha": "deadbeefcafe"},
            "url": f"{base}/repos/acme/app/pulls/42",
            "issue_url": f"{base}/repos/acme/app/issues/42",
        },
    )
    # pr.head.sha -> repo_obj.get_commit(sha) hits /repos/.../commits/<sha>.
    github_at.add("GET", "/repos/acme/app/commits/deadbeefcafe", {"sha": "deadbeefcafe", "url": f"{base}/repos/acme/app/commits/deadbeefcafe"})
    github_at.add(
        "POST",
        "/repos/acme/app/pulls/42/comments",
        {"id": 9001, "body": "[AI] nit: rename", "path": "src/app/main.py", "line": 7, "side": "RIGHT"},
        status=201,
    )

    result = GithubPrCommentWriter(company="acme").send(
        target="acme/app#42",
        body="nit: rename",
        file_path="src/app/main.py",
        line=7,
    )

    assert result.ok is True
    assert result.ref == "9001"  # the real review-comment id

    posts = [r for r in github_at.received if r["method"] == "POST" and r["path"] == "/repos/acme/app/pulls/42/comments"]
    assert posts, f"never posted review comment; received={[(r['method'], r['path']) for r in github_at.received]}"
    payload = json.loads(posts[0]["body"])
    assert payload["body"] == "[AI] nit: rename"  # with_ai_prefix applied
    assert payload["commit_id"] == "deadbeefcafe"  # resolved from pr.head.sha
    assert payload["path"] == "src/app/main.py"
    assert payload["line"] == 7
    assert payload["side"] == "RIGHT"  # default when not overridden


def test_github_inline_side_override_real(github_at) -> None:
    """extras['side'] overrides the default RIGHT in the posted payload."""
    from briar.messaging.github_pr_comment import GithubPrCommentWriter

    base = github_at.base_url
    github_at.add("GET", "/repos/acme/app", {"id": 1, "name": "app", "full_name": "acme/app", "url": f"{base}/repos/acme/app"})
    github_at.add(
        "GET",
        "/repos/acme/app/pulls/42",
        {"number": 42, "head": {"sha": "abc123"}, "url": f"{base}/repos/acme/app/pulls/42", "issue_url": f"{base}/repos/acme/app/issues/42"},
    )
    github_at.add("GET", "/repos/acme/app/commits/abc123", {"sha": "abc123", "url": f"{base}/repos/acme/app/commits/abc123"})
    github_at.add("POST", "/repos/acme/app/pulls/42/comments", {"id": 9002}, status=201)

    result = GithubPrCommentWriter(company="acme").send(
        target="acme/app#42",
        body="old-side note",
        file_path="src/app/main.py",
        line=4,
        side="LEFT",
    )

    assert result.ok is True
    payload = json.loads([r for r in github_at.received if r["method"] == "POST"][0]["body"])
    assert payload["side"] == "LEFT"


def test_github_inline_bare_slug_with_pr_extra_real(github_at) -> None:
    """Target is a bare owner/repo slug; the PR number arrives via extras['pr'].
    parse_pr_target falls back to that, then the inline path runs as normal."""
    from briar.messaging.github_pr_comment import GithubPrCommentWriter

    base = github_at.base_url
    github_at.add("GET", "/repos/acme/app", {"id": 1, "name": "app", "full_name": "acme/app", "url": f"{base}/repos/acme/app"})
    github_at.add(
        "GET",
        "/repos/acme/app/pulls/57",
        {"number": 57, "head": {"sha": "feed01"}, "url": f"{base}/repos/acme/app/pulls/57", "issue_url": f"{base}/repos/acme/app/issues/57"},
    )
    github_at.add("GET", "/repos/acme/app/commits/feed01", {"sha": "feed01", "url": f"{base}/repos/acme/app/commits/feed01"})
    github_at.add("POST", "/repos/acme/app/pulls/57/comments", {"id": 9003}, status=201)

    result = GithubPrCommentWriter(company="acme").send(
        target="acme/app",
        body="inline via extras.pr",
        pr=57,
        file_path="README.md",
        line=1,
    )

    assert result.ok is True
    assert result.ref == "9003"
    # Resolved PR #57 from extras['pr'] and posted to that PR's comments URL.
    posts = [r for r in github_at.received if r["method"] == "POST" and r["path"] == "/repos/acme/app/pulls/57/comments"]
    assert posts, f"never posted to PR 57; received={[(r['method'], r['path']) for r in github_at.received]}"
    payload = json.loads(posts[0]["body"])
    assert payload["commit_id"] == "feed01"
    assert payload["path"] == "README.md"


def test_github_inline_422_is_swallowed(github_at) -> None:
    """An invalid review-comment position -> 422 from GitHub raises in PyGithub;
    @swallow_errors maps it to a structured ok=False, not a crash."""
    from briar.messaging.github_pr_comment import GithubPrCommentWriter

    base = github_at.base_url
    github_at.add("GET", "/repos/acme/app", {"id": 1, "name": "app", "full_name": "acme/app", "url": f"{base}/repos/acme/app"})
    github_at.add(
        "GET",
        "/repos/acme/app/pulls/42",
        {"number": 42, "head": {"sha": "deadbeef"}, "url": f"{base}/repos/acme/app/pulls/42", "issue_url": f"{base}/repos/acme/app/issues/42"},
    )
    github_at.add("GET", "/repos/acme/app/commits/deadbeef", {"sha": "deadbeef", "url": f"{base}/repos/acme/app/commits/deadbeef"})
    # GitHub validation error envelope.
    github_at.add(
        "POST",
        "/repos/acme/app/pulls/42/comments",
        {"message": "Validation Failed", "errors": [{"resource": "PullRequestReviewComment", "field": "line", "code": "invalid"}]},
        status=422,
    )

    result = GithubPrCommentWriter(company="acme").send(
        target="acme/app#42",
        body="bad position",
        file_path="src/app/main.py",
        line=99999,
    )

    assert result.ok is False
    assert result.detail == "exception"
