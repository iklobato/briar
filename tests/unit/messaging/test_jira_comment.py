"""JiraCommentWriter — add a comment to a Jira ticket.

REST endpoint (wrapped by atlassian-python-api's `issue_add_comment`):
  POST /rest/api/3/issue/{issueIdOrKey}/comment
  https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/
        #api-rest-api-3-issue-issueidorkey-comment-post
  201 response includes `id` (string) + `body` + `author`.
  Error envelope: {"errorMessages": [...], "errors": {...}}.

The writer calls `self._jira().issue_add_comment(key, body)`; we patch
`_jira` to a mock client and assert the call + the returned ref.
"""

from __future__ import annotations

import pytest

from briar.messaging.jira_comment import JiraCommentWriter


def _creds(monkeypatch):
    monkeypatch.setenv("JIRA_ACME_URL", "https://acme.atlassian.net")
    monkeypatch.setenv("JIRA_ACME_EMAIL", "bot@acme.com")
    monkeypatch.setenv("JIRA_ACME_TOKEN", "tok-123")


def _mock_jira(mocker, writer, *, add_comment_return):
    client = mocker.MagicMock(name="jira")
    client.issue_add_comment.return_value = add_comment_return
    mocker.patch.object(writer, "_jira", return_value=client)
    return client


class TestAvailability:
    def test_unavailable_without_creds(self) -> None:
        assert JiraCommentWriter(company="acme").is_available() is False

    def test_unavailable_missing_token(self, monkeypatch) -> None:
        monkeypatch.setenv("JIRA_ACME_URL", "https://acme.atlassian.net")
        monkeypatch.setenv("JIRA_ACME_EMAIL", "bot@acme.com")
        assert JiraCommentWriter(company="acme").is_available() is False

    def test_available_with_full_creds(self, monkeypatch) -> None:
        _creds(monkeypatch)
        assert JiraCommentWriter(company="acme").is_available() is True

    def test_required_env_vars(self) -> None:
        names = JiraCommentWriter.required_env_vars(company="acme")
        assert names == ["JIRA_ACME_URL", "JIRA_ACME_EMAIL", "JIRA_ACME_TOKEN"]


class TestSend:
    def test_success_returns_comment_id(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = JiraCommentWriter(company="acme")
        # 201 response: id is a string in Jira's contract.
        client = _mock_jira(mocker, w, add_comment_return={"id": "10001", "body": "[AI] hi"})

        result = w.send(target="PROJ-123", body="hi")

        assert result.ok is True
        assert result.ref == "10001"
        # Body is [AI]-prefixed; ticket key passed through verbatim.
        client.issue_add_comment.assert_called_once_with("PROJ-123", "[AI] hi")

    def test_body_already_prefixed_not_doubled(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = JiraCommentWriter(company="acme")
        client = _mock_jira(mocker, w, add_comment_return={"id": "1"})
        w.send(target="PROJ-1", body="[AI] done")
        client.issue_add_comment.assert_called_once_with("PROJ-1", "[AI] done")

    def test_creds_missing_short_circuits(self, monkeypatch, mocker) -> None:
        # No creds → is_available False → returns before touching the client.
        w = JiraCommentWriter(company="acme")
        spy = mocker.patch.object(w, "_jira")
        result = w.send(target="PROJ-1", body="x")
        assert result.ok is False
        assert "creds missing" in result.detail
        spy.assert_not_called()

    def test_empty_target_rejected(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = JiraCommentWriter(company="acme")
        spy = mocker.patch.object(w, "_jira")
        result = w.send(target="", body="x")
        assert result.ok is False
        assert "TICKET-KEY" in result.detail
        spy.assert_not_called()

    def test_non_dict_response_is_failure(self, monkeypatch, mocker) -> None:
        # atlassian-python-api can return a raw Response/str on odd paths.
        _creds(monkeypatch)
        w = JiraCommentWriter(company="acme")
        _mock_jira(mocker, w, add_comment_return="<html>error</html>")
        result = w.send(target="PROJ-1", body="x")
        assert result.ok is False
        assert "non-dict" in result.detail

    def test_response_without_id_yields_empty_ref(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = JiraCommentWriter(company="acme")
        _mock_jira(mocker, w, add_comment_return={"body": "no id here"})
        result = w.send(target="PROJ-1", body="x")
        assert result.ok is True
        assert result.ref == ""


class TestClientCaching:
    def test_jira_client_built_once_and_cached(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        built = mocker.MagicMock(name="jira")
        # JiraCreds.client() builds the atlassian.Jira; patch it so no
        # real client is constructed, and assert the writer caches it.
        client_factory = mocker.patch("briar.messaging.jira_comment.JiraCreds.client", return_value=built)
        w = JiraCommentWriter(company="acme")
        assert w._jira() is built
        assert w._jira() is built
        client_factory.assert_called_once()


class TestApiFailureModes:
    """HTTPError from atlassian-python-api is a plain Exception →
    swallowed → SendResult(ok=False, detail='exception')."""

    @pytest.mark.parametrize("status", [401, 403, 404, 400, 429, 500])
    def test_http_error_is_swallowed(self, monkeypatch, mocker, status) -> None:
        import requests

        _creds(monkeypatch)
        w = JiraCommentWriter(company="acme")
        client = _mock_jira(mocker, w, add_comment_return={"id": "1"})
        # Jira error envelope:
        # {"errorMessages":["Issue does not exist..."],"errors":{}}
        resp = mocker.MagicMock()
        resp.status_code = status
        resp.json.return_value = {"errorMessages": ["nope"], "errors": {}}
        client.issue_add_comment.side_effect = requests.exceptions.HTTPError(response=resp)

        result = w.send(target="PROJ-1", body="x")

        assert result.ok is False
        assert result.detail == "exception"
        assert result.ref == ""

    def test_network_timeout_is_swallowed(self, monkeypatch, mocker) -> None:
        import requests

        _creds(monkeypatch)
        w = JiraCommentWriter(company="acme")
        client = _mock_jira(mocker, w, add_comment_return={"id": "1"})
        client.issue_add_comment.side_effect = requests.exceptions.Timeout("timed out")

        result = w.send(target="PROJ-1", body="x")

        assert result.ok is False
        assert result.detail == "exception"
