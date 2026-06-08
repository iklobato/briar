"""GithubPrCommentWriter — top-level + inline review comments.

PyGithub wraps the REST endpoints:
- top-level:  POST /repos/{o}/{r}/issues/{n}/comments
              https://docs.github.com/en/rest/issues/comments#create-an-issue-comment
              response includes `id` (int) + `html_url`.
- inline:     POST /repos/{o}/{r}/pulls/{n}/comments
              https://docs.github.com/en/rest/pulls/comments#create-a-review-comment-for-a-pull-request
              requires commit_id + path + line + side; response has `id`.

We mock at the PyGithub object level (the `GithubApi.client()` facade)
returning objects whose `.id` / `.head.sha` mirror those real fields.
Error envelopes follow GitHub's documented shape
`{"message": "...", "documentation_url": "..."}` carried on GithubException.
"""

from __future__ import annotations

import github
import pytest

from briar.messaging.github_pr_comment import GithubPrCommentWriter


def _mock_github_client(mocker):
    """Patch GithubApi.client and return (mock_client, repo, pr) so tests
    can program return values + inspect the calls the writer made."""
    pr = mocker.MagicMock(name="pull")
    # `pr.head.sha` is the commit the inline comment anchors to.
    pr.head.sha = "abc123def456"

    repo_obj = mocker.MagicMock(name="repo")
    repo_obj.get_pull.return_value = pr

    client = mocker.MagicMock(name="github")
    client.get_repo.return_value = repo_obj

    mocker.patch(
        "briar.messaging.github_pr_comment.GithubApi.client",
        return_value=client,
    )
    return client, repo_obj, pr


class TestAvailability:
    def test_unavailable_without_token(self) -> None:
        assert GithubPrCommentWriter().is_available() is False

    def test_available_with_token(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        assert GithubPrCommentWriter().is_available() is True

    def test_blank_token_is_unavailable(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "   ")
        assert GithubPrCommentWriter().is_available() is False

    def test_required_env_vars(self) -> None:
        assert GithubPrCommentWriter.required_env_vars() == ["GITHUB_TOKEN"]


class TestTopLevelComment:
    def test_success_returns_real_comment_id(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        _, repo_obj, pr = _mock_github_client(mocker)
        # Issue-comment response: real field is the int `id` (e.g. 1).
        # https://docs.github.com/en/rest/issues/comments#create-an-issue-comment
        comment = mocker.MagicMock()
        comment.id = 123456
        pr.create_issue_comment.return_value = comment

        result = GithubPrCommentWriter().send(target="octo/repo#42", body="ship it")

        assert result.ok is True
        assert result.ref == "123456"
        # Request shape: right repo, right PR number, [AI]-prefixed body.
        repo_obj.get_repo.assert_not_called()  # client.get_repo, not repo.get_repo
        pr.create_issue_comment.assert_called_once_with("[AI] ship it")
        # No review comment on the top-level path.
        pr.create_review_comment.assert_not_called()

    def test_resolves_repo_and_pull_from_target(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        client, repo_obj, pr = _mock_github_client(mocker)
        pr.create_issue_comment.return_value = mocker.MagicMock(id=1)

        GithubPrCommentWriter().send(target="octo/hello-world#42", body="hi")

        client.get_repo.assert_called_once_with("octo/hello-world")
        repo_obj.get_pull.assert_called_once_with(42)

    def test_body_already_prefixed_not_doubled(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        _, _, pr = _mock_github_client(mocker)
        pr.create_issue_comment.return_value = mocker.MagicMock(id=1)

        GithubPrCommentWriter().send(target="octo/repo#1", body="[AI] already")

        pr.create_issue_comment.assert_called_once_with("[AI] already")

    def test_pr_number_from_extras(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        _, repo_obj, pr = _mock_github_client(mocker)
        pr.create_issue_comment.return_value = mocker.MagicMock(id=7)

        result = GithubPrCommentWriter().send(target="octo/repo", body="x", pr=8)

        assert result.ok is True
        repo_obj.get_pull.assert_called_once_with(8)


class TestInlineReviewComment:
    def test_inline_success_resolves_head_sha_and_defaults(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        _, repo_obj, pr = _mock_github_client(mocker)
        commit_obj = mocker.MagicMock(name="commit")
        repo_obj.get_commit.return_value = commit_obj
        review = mocker.MagicMock()
        review.id = 9988
        pr.create_review_comment.return_value = review

        result = GithubPrCommentWriter().send(
            target="octo/repo#42",
            body="nit here",
            file_path="src/app.py",
        )

        assert result.ok is True
        assert result.ref == "9988"
        # Head SHA resolved from pr.head.sha; commit fetched for that SHA.
        repo_obj.get_commit.assert_called_once_with("abc123def456")
        # line defaults to 1, side defaults to RIGHT, body [AI]-prefixed.
        pr.create_review_comment.assert_called_once_with(
            body="[AI] nit here",
            commit=commit_obj,
            path="src/app.py",
            line=1,
            side="RIGHT",
        )
        # Inline path must NOT post a top-level comment.
        pr.create_issue_comment.assert_not_called()

    def test_inline_honours_explicit_line_and_side(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        _, repo_obj, pr = _mock_github_client(mocker)
        repo_obj.get_commit.return_value = mocker.MagicMock()
        pr.create_review_comment.return_value = mocker.MagicMock(id=1)

        GithubPrCommentWriter().send(
            target="octo/repo#42",
            body="left side",
            file_path="a.py",
            line=120,
            side="LEFT",
        )

        _, kwargs = pr.create_review_comment.call_args
        assert kwargs["line"] == 120
        assert kwargs["side"] == "LEFT"

    def test_inline_line_zero_falls_back_to_one(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        _, repo_obj, pr = _mock_github_client(mocker)
        repo_obj.get_commit.return_value = mocker.MagicMock()
        pr.create_review_comment.return_value = mocker.MagicMock(id=1)

        GithubPrCommentWriter().send(target="octo/repo#42", body="x", file_path="a.py", line=0)

        _, kwargs = pr.create_review_comment.call_args
        assert kwargs["line"] == 1

    def test_inline_cannot_resolve_head_sha(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        _, repo_obj, pr = _mock_github_client(mocker)
        pr.head.sha = ""  # detached / unresolvable head

        result = GithubPrCommentWriter().send(target="octo/repo#42", body="x", file_path="a.py")

        assert result.ok is False
        assert "head sha" in result.detail.lower()
        # Bailed before fetching the commit or posting.
        repo_obj.get_commit.assert_not_called()
        pr.create_review_comment.assert_not_called()


class TestTargetParsing:
    def test_malformed_target_no_number(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        result = GithubPrCommentWriter().send(target="octo/repo", body="x")
        assert result.ok is False
        assert "owner/repo#N" in result.detail
        assert "octo/repo" in result.detail

    def test_malformed_target_non_numeric(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        result = GithubPrCommentWriter().send(target="octo/repo#abc", body="x")
        assert result.ok is False
        assert result.ref == ""

    def test_empty_target(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        result = GithubPrCommentWriter().send(target="", body="x")
        assert result.ok is False


class TestApiFailureModes:
    """Every GithubException is a plain Exception → swallowed by
    @swallow_errors → SendResult(ok=False, detail='exception')."""

    @pytest.mark.parametrize(
        "exc",
        [
            # 401 bad credentials — GitHub envelope:
            # {"message":"Bad credentials","documentation_url":"https://docs.github.com/rest"}
            github.BadCredentialsException(
                401,
                {"message": "Bad credentials", "documentation_url": "https://docs.github.com/rest"},
                {},
            ),
            # 403 forbidden (e.g. insufficient scope / SAML).
            github.GithubException(
                403,
                {"message": "Forbidden", "documentation_url": "https://docs.github.com/rest"},
                {},
            ),
            # 404 repo or PR not found.
            github.UnknownObjectException(
                404,
                {"message": "Not Found", "documentation_url": "https://docs.github.com/rest/pulls"},
                {},
            ),
            # 422 validation (e.g. invalid line for review comment).
            github.GithubException(
                422,
                {
                    "message": "Validation Failed",
                    "errors": [{"resource": "PullRequestReviewComment", "code": "invalid", "field": "line"}],
                    "documentation_url": "https://docs.github.com/rest/pulls/comments",
                },
                {},
            ),
            # 429 / secondary rate limit.
            github.RateLimitExceededException(
                429,
                {"message": "API rate limit exceeded", "documentation_url": "https://docs.github.com/rest"},
                {},
            ),
            # 5xx server error.
            github.GithubException(502, {"message": "Server Error"}, {}),
        ],
        ids=["401", "403", "404", "422", "429", "502"],
    )
    def test_top_level_api_error_is_swallowed(self, monkeypatch, mocker, exc) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        _, _, pr = _mock_github_client(mocker)
        pr.create_issue_comment.side_effect = exc

        result = GithubPrCommentWriter().send(target="octo/repo#42", body="x")

        assert result.ok is False
        assert result.detail == "exception"
        assert result.ref == ""

    def test_repo_not_found_at_get_repo(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        client, _, _ = _mock_github_client(mocker)
        client.get_repo.side_effect = github.UnknownObjectException(404, {"message": "Not Found"}, {})

        result = GithubPrCommentWriter().send(target="ghost/repo#1", body="x")

        assert result.ok is False
        assert result.detail == "exception"

    def test_network_timeout_is_swallowed(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        _, _, pr = _mock_github_client(mocker)
        # PyGithub surfaces socket-level timeouts; any non-caller-error
        # Exception is swallowed to ok=False.
        pr.create_issue_comment.side_effect = TimeoutError("read timed out")

        result = GithubPrCommentWriter().send(target="octo/repo#42", body="x")

        assert result.ok is False
        assert result.detail == "exception"

    def test_inline_api_error_is_swallowed(self, monkeypatch, mocker) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
        _, repo_obj, pr = _mock_github_client(mocker)
        repo_obj.get_commit.return_value = mocker.MagicMock()
        # 422 invalid line is a common inline-comment failure.
        pr.create_review_comment.side_effect = github.GithubException(
            422,
            {"message": "Validation Failed", "documentation_url": "https://docs.github.com/rest/pulls/comments"},
            {},
        )

        result = GithubPrCommentWriter().send(target="octo/repo#42", body="x", file_path="a.py", line=5)

        assert result.ok is False
        assert result.detail == "exception"
