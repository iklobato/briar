"""JiraTransitionWriter — move a ticket to a target status.

REST endpoint (wrapped by atlassian-python-api's `set_issue_status`,
which resolves the transition id then POSTs):
  POST /rest/api/3/issue/{issueIdOrKey}/transitions
  https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/
        #api-rest-api-3-issue-issueidorkey-transitions-post
  204 No Content on success (no body) — hence the writer treats `None`
  as success. Error envelope: {"errorMessages": [...], "errors": {...}}.

The writer calls `self._jira().set_issue_status(key, status, update=...)`
(a resolution comment rides the transition POST via the `update` field).
"""

from __future__ import annotations

import pytest

from briar.messaging.jira_transition import JiraTransitionWriter


def _creds(monkeypatch):
    monkeypatch.setenv("JIRA_ACME_URL", "https://acme.atlassian.net")
    monkeypatch.setenv("JIRA_ACME_EMAIL", "bot@acme.com")
    monkeypatch.setenv("JIRA_ACME_TOKEN", "tok-123")


def _mock_jira(mocker, writer, *, set_status_return):
    client = mocker.MagicMock(name="jira")
    client.set_issue_status.return_value = set_status_return
    mocker.patch.object(writer, "_jira", return_value=client)
    return client


class TestAvailability:
    def test_unavailable_without_creds(self) -> None:
        assert JiraTransitionWriter(company="acme").is_available() is False

    def test_available_with_full_creds(self, monkeypatch) -> None:
        _creds(monkeypatch)
        assert JiraTransitionWriter(company="acme").is_available() is True

    def test_required_env_vars(self) -> None:
        names = JiraTransitionWriter.required_env_vars(company="acme")
        assert names == ["JIRA_ACME_URL", "JIRA_ACME_EMAIL", "JIRA_ACME_TOKEN"]


class TestSend:
    def test_success_with_status_from_extras(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = JiraTransitionWriter(company="acme")
        # set_issue_status returns None on the 204 happy path.
        client = _mock_jira(mocker, w, set_status_return=None)

        result = w.send(target="PROJ-7", body="resolving", status="Done")

        assert result.ok is True
        assert result.ref == "PROJ-7→Done"
        # Comment is [AI]-prefixed and rides the transition POST via `update`
        # (the atlassian client's set_issue_status has no `comment` param).
        client.set_issue_status.assert_called_once_with("PROJ-7", "Done", update={"comment": [{"add": {"body": "[AI] resolving"}}]})

    def test_status_from_binding_config_default(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = JiraTransitionWriter(company="acme", config={"status": "In Review"})
        client = _mock_jira(mocker, w, set_status_return=None)

        result = w.send(target="PROJ-9", body="")

        assert result.ok is True
        assert result.ref == "PROJ-9→In Review"
        # Empty body → no update payload (no resolution note).
        client.set_issue_status.assert_called_once_with("PROJ-9", "In Review", update=None)

    def test_extras_status_overrides_config_default(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = JiraTransitionWriter(company="acme", config={"status": "Done"})
        client = _mock_jira(mocker, w, set_status_return=None)
        w.send(target="PROJ-1", body="", status="Blocked")
        client.set_issue_status.assert_called_once_with("PROJ-1", "Blocked", update=None)

    def test_non_none_response_used_as_ref(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = JiraTransitionWriter(company="acme")
        _mock_jira(mocker, w, set_status_return={"transition": "ok"})
        result = w.send(target="PROJ-1", body="", status="Done")
        assert result.ok is True
        assert result.ref == str({"transition": "ok"})[:200]

    def test_ref_truncated_to_200_chars(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = JiraTransitionWriter(company="acme")
        _mock_jira(mocker, w, set_status_return="X" * 500)
        result = w.send(target="PROJ-1", body="", status="Done")
        assert len(result.ref) == 200


class TestSendValidation:
    def test_creds_missing_short_circuits(self, mocker) -> None:
        w = JiraTransitionWriter(company="acme")
        spy = mocker.patch.object(w, "_jira")
        result = w.send(target="PROJ-1", body="x", status="Done")
        assert result.ok is False
        assert "creds missing" in result.detail
        spy.assert_not_called()

    def test_empty_target_rejected(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        w = JiraTransitionWriter(company="acme")
        spy = mocker.patch.object(w, "_jira")
        result = w.send(target="", body="x", status="Done")
        assert result.ok is False
        assert "TICKET-KEY" in result.detail
        spy.assert_not_called()

    def test_missing_status_rejected(self, monkeypatch, mocker) -> None:
        # No extras.status and no config.status → can't pick a transition.
        _creds(monkeypatch)
        w = JiraTransitionWriter(company="acme")
        spy = mocker.patch.object(w, "_jira")
        result = w.send(target="PROJ-1", body="x")
        assert result.ok is False
        assert "status" in result.detail.lower()
        spy.assert_not_called()


class TestClientCaching:
    def test_jira_client_built_once_and_cached(self, monkeypatch, mocker) -> None:
        _creds(monkeypatch)
        built = mocker.MagicMock(name="jira")
        client_factory = mocker.patch("briar.messaging.jira_transition.JiraCreds.client", return_value=built)
        w = JiraTransitionWriter(company="acme")
        assert w._jira() is built
        assert w._jira() is built
        client_factory.assert_called_once()


class TestApiFailureModes:
    """A failed transition (e.g. invalid transition for the issue's status,
    or auth error) raises HTTPError → swallowed → ok=False."""

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
    def test_http_error_is_swallowed(self, monkeypatch, mocker, status) -> None:
        import requests

        _creds(monkeypatch)
        w = JiraTransitionWriter(company="acme")
        client = _mock_jira(mocker, w, set_status_return=None)
        # e.g. 400 when the status name doesn't match an available transition.
        resp = mocker.MagicMock()
        resp.status_code = status
        resp.json.return_value = {
            "errorMessages": [],
            "errors": {"transition": "It is not on the appropriate workflow."},
        }
        client.set_issue_status.side_effect = requests.exceptions.HTTPError(response=resp)

        result = w.send(target="PROJ-1", body="x", status="Done")

        assert result.ok is False
        assert result.detail == "exception"
        assert result.ref == ""

    def test_network_timeout_is_swallowed(self, monkeypatch, mocker) -> None:
        import requests

        _creds(monkeypatch)
        w = JiraTransitionWriter(company="acme")
        client = _mock_jira(mocker, w, set_status_return=None)
        client.set_issue_status.side_effect = requests.exceptions.Timeout("timed out")

        result = w.send(target="PROJ-1", body="x", status="Done")

        assert result.ok is False
        assert result.detail == "exception"
