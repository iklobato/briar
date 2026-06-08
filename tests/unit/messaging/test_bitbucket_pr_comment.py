"""BitbucketPrCommentWriter — Bitbucket Cloud PR comments.

REST endpoint (wrapped by atlassian-python-api Cloud client):
  POST /2.0/repositories/{workspace}/{repo}/pullrequests/{id}/comments
  https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pullrequests/
        #api-repositories-workspace-repo-slug-pullrequests-pull-request-id-comments-post
  Body: {"content": {"raw": "..."}}  (inline adds {"inline": {"path","to"}}).
  201 response includes `id` (int) + `content` + `links`.

The writer reaches the post via:
  cloud.workspaces.get(ws).repositories.get(repo).post("pullrequests/N/comments", data=...)
so we mock that chain and assert the path + payload.
"""

from __future__ import annotations

import pytest

from briar.messaging.bitbucket_pr_comment import BitbucketPrCommentWriter


def _creds(monkeypatch, *, username="bot", password="app-pass", workspace="acmews"):
    if username is not None:
        monkeypatch.setenv("BITBUCKET_ACME_USERNAME", username)
    if password is not None:
        monkeypatch.setenv("BITBUCKET_ACME_APP_PASSWORD", password)
    if workspace is not None:
        monkeypatch.setenv("BITBUCKET_ACME_WORKSPACE", workspace)


def _mock_cloud(mocker, writer, *, post_return):
    """Patch the writer's `_cloud()` to return a mock whose
    workspaces.get(...).repositories.get(...).post(...) yields `post_return`.
    Returns the repo mock so tests can inspect `.post` calls."""
    bb_repo = mocker.MagicMock(name="bb_repo")
    bb_repo.post.return_value = post_return

    repos = mocker.MagicMock()
    repos.get.return_value = bb_repo
    workspace = mocker.MagicMock()
    workspace.repositories = repos
    workspaces = mocker.MagicMock()
    workspaces.get.return_value = workspace
    cloud = mocker.MagicMock()
    cloud.workspaces = workspaces

    mocker.patch.object(writer, "_cloud", return_value=cloud)
    return cloud, workspaces, repos, bb_repo


class TestAvailability:
    def test_unavailable_without_creds(self) -> None:
        assert BitbucketPrCommentWriter(company="acme").is_available() is False

    def test_unavailable_missing_workspace(self, monkeypatch) -> None:
        _creds(monkeypatch, workspace=None)
        assert BitbucketPrCommentWriter(company="acme").is_available() is False

    def test_available_with_full_creds(self, monkeypatch) -> None:
        _creds(monkeypatch)
        assert BitbucketPrCommentWriter(company="acme").is_available() is True

    def test_unavailable_without_company(self, monkeypatch) -> None:
        # Templated creds need a company; empty company can't resolve them.
        assert BitbucketPrCommentWriter(company="").is_available() is False

    def test_required_env_vars(self) -> None:
        names = BitbucketPrCommentWriter.required_env_vars(company="acme")
        assert names == [
            "BITBUCKET_ACME_USERNAME",
            "BITBUCKET_ACME_APP_PASSWORD",
            "BITBUCKET_ACME_WORKSPACE",
        ]

    def test_required_env_vars_empty_without_company(self) -> None:
        assert BitbucketPrCommentWriter.required_env_vars(company="") == []


class TestTopLevelComment:
    def test_success_returns_id_and_posts_raw_body(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = BitbucketPrCommentWriter(company="acme")
        # 201 response shape: {"id": 12345, "content": {"raw": ...}, ...}
        _, workspaces, repos, bb_repo = _mock_cloud(mocker, w, post_return={"id": 12345, "content": {"raw": "[AI] hi"}})

        result = w.send(target="myws/myrepo#42", body="hi")

        assert result.ok is True
        assert result.ref == "12345"
        # Workspace + repo resolved from the target slug.
        workspaces.get.assert_called_once_with("myws")
        repos.get.assert_called_once_with("myrepo")
        # Path + payload: top-level has no `inline`, body is [AI]-prefixed.
        bb_repo.post.assert_called_once_with("pullrequests/42/comments", data={"content": {"raw": "[AI] hi"}})

    def test_id_as_int_stringified(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = BitbucketPrCommentWriter(company="acme")
        _mock_cloud(mocker, w, post_return={"id": 7})
        result = w.send(target="ws/repo#1", body="x")
        assert result.ref == "7"

    def test_pr_number_from_extras(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = BitbucketPrCommentWriter(company="acme")
        _, _, _, bb_repo = _mock_cloud(mocker, w, post_return={"id": 1})
        w.send(target="ws/repo", body="x", pr=99)
        bb_repo.post.assert_called_once()
        assert bb_repo.post.call_args.args[0] == "pullrequests/99/comments"

    def test_bare_repo_falls_back_to_workspace_slug(self, monkeypatch, mocker) -> None:
        # target "repo#5" has no "/" → workspace_slug from creds (acmews).
        _creds(monkeypatch, workspace="acmews")
        w = BitbucketPrCommentWriter(company="acme")
        _, workspaces, repos, _ = _mock_cloud(mocker, w, post_return={"id": 1})
        w.send(target="repo#5", body="x")
        workspaces.get.assert_called_once_with("acmews")
        repos.get.assert_called_once_with("repo")

    def test_missing_id_in_response_yields_empty_ref(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = BitbucketPrCommentWriter(company="acme")
        _mock_cloud(mocker, w, post_return={"content": {"raw": "[AI] x"}})
        result = w.send(target="ws/repo#1", body="x")
        assert result.ok is True
        assert result.ref == ""

    def test_non_dict_response_yields_empty_ref(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = BitbucketPrCommentWriter(company="acme")
        _mock_cloud(mocker, w, post_return=None)
        result = w.send(target="ws/repo#1", body="x")
        assert result.ok is True
        assert result.ref == ""


class TestInlineComment:
    def test_inline_payload_has_path_and_line(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = BitbucketPrCommentWriter(company="acme")
        _, _, _, bb_repo = _mock_cloud(mocker, w, post_return={"id": 5})

        w.send(target="ws/repo#3", body="nit", file_path="src/x.py", line=22)

        bb_repo.post.assert_called_once_with(
            "pullrequests/3/comments",
            data={"content": {"raw": "[AI] nit"}, "inline": {"path": "src/x.py", "to": 22}},
        )

    def test_inline_line_defaults_to_one(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = BitbucketPrCommentWriter(company="acme")
        _, _, _, bb_repo = _mock_cloud(mocker, w, post_return={"id": 5})
        w.send(target="ws/repo#3", body="x", file_path="a.py")
        payload = bb_repo.post.call_args.kwargs["data"]
        assert payload["inline"] == {"path": "a.py", "to": 1}


class TestTargetParsing:
    def test_malformed_no_number(self, monkeypatch) -> None:
        _creds(monkeypatch)
        result = BitbucketPrCommentWriter(company="acme").send(target="ws/repo", body="x")
        assert result.ok is False
        assert "workspace/repo#N" in result.detail
        assert "ws/repo" in result.detail

    def test_malformed_non_numeric(self, monkeypatch) -> None:
        _creds(monkeypatch)
        result = BitbucketPrCommentWriter(company="acme").send(target="ws/repo#xyz", body="x")
        assert result.ok is False

    def test_empty_target(self, monkeypatch) -> None:
        _creds(monkeypatch)
        result = BitbucketPrCommentWriter(company="acme").send(target="", body="x")
        assert result.ok is False


class TestCloudClientConstruction:
    """Pin the auth-branch + lazy caching in `_cloud()` by mocking the
    atlassian Cloud constructor (no real network/auth)."""

    def test_basic_auth_uses_username_password(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch, username="bot", password="app-pass")
        ctor = mocker.patch("atlassian.bitbucket.cloud.Cloud", autospec=True)
        w = BitbucketPrCommentWriter(company="acme")
        w._cloud()
        ctor.assert_called_once_with(
            url="https://api.bitbucket.org/",
            username="bot",
            password="app-pass",
            timeout=20,
        )

    def test_token_auth_branch(self, monkeypatch, mocker) -> None:
        # username == "x-token-auth" selects the token= constructor form.
        _creds(monkeypatch, username="x-token-auth", password="repo-token")
        ctor = mocker.patch("atlassian.bitbucket.cloud.Cloud", autospec=True)
        w = BitbucketPrCommentWriter(company="acme")
        w._cloud()
        ctor.assert_called_once_with(
            url="https://api.bitbucket.org/",
            token="repo-token",
            timeout=20,
        )

    def test_client_is_cached(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        ctor = mocker.patch("atlassian.bitbucket.cloud.Cloud", autospec=True)
        w = BitbucketPrCommentWriter(company="acme")
        first = w._cloud()
        second = w._cloud()
        assert first is second
        ctor.assert_called_once()


class TestApiFailureModes:
    """atlassian-python-api raises requests.HTTPError (an Exception, not a
    caller-error) → swallowed → SendResult(ok=False, detail='exception')."""

    @pytest.mark.parametrize("status", [401, 403, 404, 400, 429, 500])
    def test_http_error_is_swallowed(self, monkeypatch, mocker, status) -> None:
        import requests

        _creds(monkeypatch)
        w = BitbucketPrCommentWriter(company="acme")
        _, _, _, bb_repo = _mock_cloud(mocker, w, post_return={"id": 1})
        # Bitbucket error envelope:
        # {"type":"error","error":{"message":"..."}}
        # https://developer.atlassian.com/cloud/bitbucket/rest/intro/#error-responses
        resp = mocker.MagicMock()
        resp.status_code = status
        resp.json.return_value = {"type": "error", "error": {"message": "boom"}}
        bb_repo.post.side_effect = requests.exceptions.HTTPError(response=resp)

        result = w.send(target="ws/repo#1", body="x")

        assert result.ok is False
        assert result.detail == "exception"
        assert result.ref == ""

    def test_network_timeout_is_swallowed(self, monkeypatch, mocker) -> None:
        import requests

        _creds(monkeypatch)
        w = BitbucketPrCommentWriter(company="acme")
        _, _, _, bb_repo = _mock_cloud(mocker, w, post_return={"id": 1})
        bb_repo.post.side_effect = requests.exceptions.Timeout("read timed out")

        result = w.send(target="ws/repo#1", body="x")

        assert result.ok is False
        assert result.detail == "exception"
