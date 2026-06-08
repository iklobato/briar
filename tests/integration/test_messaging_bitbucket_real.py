"""End-to-end: the REAL Bitbucket Cloud writer (atlassian-python-api over
`requests`) posting a PR comment against a wire-level mock, so target parsing,
the workspace->repo hypermedia traversal, the with_ai_prefix body, the real
client POST, and the SendResult mapping all run.

Bitbucket Cloud PR comments:
  POST /2.0/repositories/{workspace}/{repo}/pullrequests/{id}/comments
  https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pullrequests/
        #api-repositories-workspace-repo-slug-pullrequests-pull-request-id-comments-post
  201 Created on success; the response carries the new comment `id` (int).

The atlassian Cloud client follows hypermedia: it GETs the workspace, follows
its `links.repositories.href` to GET the repo, then POSTs relative to the
repo's `links.self.href`. Those links must point back at the mock server.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def _seed_repo(bitbucket_at) -> str:
    base = bitbucket_at.base_url
    bitbucket_at.add(
        "GET",
        "/2.0/workspaces/acme-ws",
        {
            "type": "workspace",
            "slug": "acme-ws",
            "uuid": "{ws-uuid}",
            "links": {
                "self": {"href": f"{base}/2.0/workspaces/acme-ws"},
                "repositories": {"href": f"{base}/2.0/repositories/acme-ws"},
            },
        },
    )
    bitbucket_at.add(
        "GET",
        "/2.0/repositories/acme-ws/app",
        {
            "type": "repository",
            "slug": "app",
            "uuid": "{repo-uuid}",
            "full_name": "acme-ws/app",
            "links": {"self": {"href": f"{base}/2.0/repositories/acme-ws/app"}},
        },
    )
    return base


def test_bitbucket_pr_comment_real_top_level(bitbucket_at) -> None:
    from briar.messaging.bitbucket_pr_comment import BitbucketPrCommentWriter

    _seed_repo(bitbucket_at)
    bitbucket_at.add(
        "POST",
        "/2.0/repositories/acme-ws/app/pullrequests/42/comments",
        {"id": 777, "type": "pullrequest_comment", "content": {"raw": "[AI] please review"}},
        status=201,
    )

    result = BitbucketPrCommentWriter(company="acme").send(target="acme-ws/app#42", body="please review")

    assert result.ok is True
    assert result.ref == "777"  # the real comment id from the 201 body

    posts = [r for r in bitbucket_at.received if r["method"] == "POST" and r["path"].endswith("/pullrequests/42/comments")]
    assert posts, f"never posted; received={[(r['method'], r['path']) for r in bitbucket_at.received]}"
    payload = json.loads(posts[0]["body"])
    # with_ai_prefix applied + the Bitbucket content envelope, no inline block.
    assert payload == {"content": {"raw": "[AI] please review"}}
    assert "inline" not in payload


def test_bitbucket_pr_comment_real_inline(bitbucket_at) -> None:
    """file_path + line set -> the payload carries the `inline` anchor block."""
    from briar.messaging.bitbucket_pr_comment import BitbucketPrCommentWriter

    _seed_repo(bitbucket_at)
    bitbucket_at.add(
        "POST",
        "/2.0/repositories/acme-ws/app/pullrequests/42/comments",
        {"id": 778, "type": "pullrequest_comment"},
        status=201,
    )

    result = BitbucketPrCommentWriter(company="acme").send(
        target="acme-ws/app#42",
        body="nit: rename this",
        file_path="src/app/main.py",
        line=12,
    )

    assert result.ok is True
    assert result.ref == "778"
    posts = [r for r in bitbucket_at.received if r["method"] == "POST" and r["path"].endswith("/comments")]
    payload = json.loads(posts[0]["body"])
    assert payload["content"]["raw"] == "[AI] nit: rename this"
    assert payload["inline"] == {"path": "src/app/main.py", "to": 12}


def test_bitbucket_pr_comment_real_404_is_swallowed(bitbucket_at) -> None:
    """A 404 on the repo lookup (deleted/typo'd repo) raises in the real
    client; @swallow_errors turns it into a structured ok=False, not a crash."""
    from briar.messaging.bitbucket_pr_comment import BitbucketPrCommentWriter

    base = bitbucket_at.base_url
    bitbucket_at.add(
        "GET",
        "/2.0/workspaces/acme-ws",
        {
            "type": "workspace",
            "slug": "acme-ws",
            "links": {
                "self": {"href": f"{base}/2.0/workspaces/acme-ws"},
                "repositories": {"href": f"{base}/2.0/repositories/acme-ws"},
            },
        },
    )
    # Bitbucket's documented error envelope: {"type": "error", "error": {...}}.
    bitbucket_at.add(
        "GET",
        "/2.0/repositories/acme-ws/app",
        {"type": "error", "error": {"message": "Repository acme-ws/app not found"}},
        status=404,
    )

    result = BitbucketPrCommentWriter(company="acme").send(target="acme-ws/app#42", body="hi")

    assert result.ok is False
    assert result.detail == "exception"
    # Never reached the comments endpoint.
    assert not [r for r in bitbucket_at.received if r["method"] == "POST"]


def test_bitbucket_pr_comment_real_401_is_swallowed(bitbucket_at) -> None:
    """Bad app password -> 401 on the first call -> swallowed ok=False."""
    from briar.messaging.bitbucket_pr_comment import BitbucketPrCommentWriter

    bitbucket_at.add(
        "GET",
        "/2.0/workspaces/acme-ws",
        {"type": "error", "error": {"message": "Access token expired."}},
        status=401,
    )

    result = BitbucketPrCommentWriter(company="acme").send(target="acme-ws/app#42", body="hi")

    assert result.ok is False
    assert result.detail == "exception"
