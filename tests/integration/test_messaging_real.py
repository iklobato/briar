"""End-to-end: the REAL message writers (PyGithub / atlassian) posting against
wire-level mocks, so target parsing, the with_ai_prefix body, the real client
call, and SendResult mapping all run.

GitHub issue comments: https://docs.github.com/en/rest/issues/comments#create-an-issue-comment
Jira comments: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def test_github_pr_comment_real_pygithub(github_at) -> None:
    from briar.messaging.github_pr_comment import GithubPrCommentWriter

    # PyGithub resolves the repo + PR, then POSTs the issue comment.
    # PyGithub follows hypermedia for writes: it builds the comment URL from the
    # PR response's `issue_url`, so those must point back at the mock server.
    base = github_at.base_url
    github_at.add("GET", "/repos/acme/app", {"id": 1, "name": "app", "full_name": "acme/app", "url": f"{base}/repos/acme/app"})
    github_at.add(
        "GET",
        "/repos/acme/app/pulls/42",
        {"number": 42, "title": "x", "head": {"sha": "deadbeef"}, "url": f"{base}/repos/acme/app/pulls/42", "issue_url": f"{base}/repos/acme/app/issues/42"},
    )
    github_at.add(
        "POST",
        "/repos/acme/app/issues/42/comments",
        {"id": 555, "body": "posted", "html_url": "https://github.com/acme/app/pull/42#issuecomment-555"},
        status=201,
    )

    result = GithubPrCommentWriter(company="acme").send(target="acme/app#42", body="please review")

    assert result.ok is True
    assert result.ref == "555"  # the real comment id from the API response
    posts = [r for r in github_at.received if r["method"] == "POST" and "/issues/42/comments" in r["path"]]
    assert posts, f"never posted; received={[(r['method'], r['path']) for r in github_at.received]}"
    body = json.loads(posts[0]["body"])["body"]
    assert "please review" in body  # the with_ai_prefix-wrapped body carried through


def test_github_pr_comment_real_404_is_swallowed(github_at) -> None:
    from briar.messaging.github_pr_comment import GithubPrCommentWriter

    github_at.add("GET", "/repos/acme/app", {"message": "Not Found", "documentation_url": "https://docs.github.com/rest"}, status=404)

    result = GithubPrCommentWriter(company="acme").send(target="acme/app#42", body="hi")
    assert result.ok is False  # @swallow_errors -> structured failure, not a crash
