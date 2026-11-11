"""The adapter verbs `briar serve` adds (spec 003 PR 1: S9, S29): the tickets assigned to
the account of the token, and the write check. The vendor clients are fakes at their edge."""

from __future__ import annotations

from typing import Any, Dict, Mapping
from unittest import mock

import pytest

from briar.auth.check import LoginRejected
from briar.extract._providers import make_provider
from briar.extract._trackers import make_tracker
from tests.unit.fake_logins import FakeLogins

FAKE = "PLACEHOLDER-not-a-secret"
MAX_TICKETS = 10
ISSUE_NUMBER = 7
PULL_NUMBER = 8
UNPROCESSABLE = 422


class Logins:
    def github(self) -> Any:
        return FakeLogins(environ={"BRIAR_ACME_GITHUB_TOKEN": FAKE}).logins()

    def jira(self) -> Any:
        return FakeLogins({"jira": {"site": "acme.atlassian.net"}}, {"BRIAR_ACME_JIRA_EMAIL": "bot@example.test", "BRIAR_ACME_JIRA_TOKEN": FAKE}).logins()


class JiraClient:
    """The atlassian `Jira` client: `post` gives one search answer, `get` the permissions."""

    def client(self, granted: Mapping[str, bool]) -> mock.MagicMock:
        fields = {"summary": "Fix login", "priority": {"name": "High"}, "created": "2026-01-02", "status": {"name": "To Do"}}
        client = mock.MagicMock()
        client.post.return_value = {"issues": [{"key": "ENG-1", "fields": fields}]}
        client.get.return_value = {"permissions": {name: {"havePermission": have} for name, have in granted.items()}}
        return client


class TestJira:
    def test_assigned_asks_for_the_open_tickets_of_the_token_account(self) -> None:
        client = JiraClient().client({})

        with mock.patch("atlassian.Jira", return_value=client):
            tickets = make_tracker("jira", Logins().jira()).assigned("ENG", MAX_TICKETS)

        assert [(ticket.key, ticket.priority) for ticket in tickets] == [("ENG-1", "High")]
        assert client.post.call_args.args == ("rest/api/3/search/jql",)
        assert client.post.call_args.kwargs["data"]["jql"] == (
            'project = "ENG" AND assignee = currentUser() AND statusCategory != "Done" ORDER BY priority DESC, created ASC'
        )

    def test_can_write_names_the_missing_permission(self) -> None:
        client = JiraClient().client({"ADD_COMMENTS": True, "TRANSITION_ISSUES": False})

        with mock.patch("atlassian.Jira", return_value=client):
            with pytest.raises(LoginRejected, match=r"^missing the Jira permission TRANSITION_ISSUES$"):
                make_tracker("jira", Logins().jira()).can_write("ENG", ["ADD_COMMENTS", "TRANSITION_ISSUES"])

        assert client.get.call_args == mock.call("rest/api/2/mypermissions", params={"projectKey": "ENG", "permissions": "ADD_COMMENTS,TRANSITION_ISSUES"})

    def test_can_write_passes_with_every_permission(self) -> None:
        with mock.patch("atlassian.Jira", return_value=JiraClient().client({"ADD_COMMENTS": True})):
            assert make_tracker("jira", Logins().jira()).can_write("ENG", ["ADD_COMMENTS"]) is None


class TestGithub:
    def test_assigned_issues_leave_the_pull_requests_out(self) -> None:
        rows = [
            {"number": ISSUE_NUMBER, "title": "Fix it", "state": "open", "labels": [{"name": "p0"}], "created_at": "2026-01-01"},
            {"number": PULL_NUMBER, "title": "A PR", "state": "open", "pull_request": {"url": "x"}},
        ]

        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows) as listed:
            tickets = make_tracker("github-issues", Logins().github()).assigned("acme/app", "octo")

        assert [(ticket.key, ticket.labels) for ticket in tickets] == [("#7", ["p0"])]
        assert listed.call_args.args == ("/repos/acme/app/issues?state=open&assignee=octo&sort=created&direction=asc",)

    @pytest.mark.parametrize("permissions", [{"push": False}, {}], ids=["no push", "no permissions"])
    def test_can_write_needs_push(self, permissions: Dict[str, bool]) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value={"permissions": permissions}):
            with pytest.raises(LoginRejected, match=r"^the token cannot push: give it Contents and Pull requests read and write$"):
                make_provider("github", Logins().github()).can_write("acme/app")

    def test_can_write_passes_with_push(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value={"permissions": {"push": True}}) as read:
            assert make_provider("github", Logins().github()).can_write("acme/app") is None

        assert read.call_args.args == ("/repos/acme/app",)

    def test_a_refused_pull_request_is_a_login_rejected_with_the_status(self) -> None:
        """PyGithub raises `GithubException`; `serve` needs a `CliError` to stop the task with a report."""
        from github import GithubException

        from briar.extract._provider import PullRequestDraft

        client = mock.MagicMock()
        refusal = GithubException(UNPROCESSABLE, {"message": "Validation Failed"}, None)
        client.get_repo.return_value.create_pull.side_effect = refusal

        with mock.patch("briar.extract._gh.GithubApi.client", return_value=client):
            with pytest.raises(LoginRejected, match=r'^HTTP 422: 422 \{"message": "Validation Failed"\}$'):
                make_provider("github", Logins().github()).open_pull("acme/app", PullRequestDraft("briar/ENG-1", "main", "t", "b"))
