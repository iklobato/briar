"""The adapter verbs `briar serve` adds (spec 003 PR 1: S9, S29): the tickets assigned to
the account of the token, and the write check. The vendor clients are fakes at their edge."""

from __future__ import annotations

from typing import Any, Dict, Mapping
from unittest import mock

import pytest

from briar.auth.check import LoginRejected
from briar.extract._provider import PullState, ThreadReply
from briar.extract._providers import make_provider
from briar.extract._trackers import make_tracker
from tests.unit.fake_logins import FakeLogins

FAKE = "PLACEHOLDER-not-a-secret"
MAX_TICKETS = 10
ISSUE_NUMBER = 7
PULL_NUMBER = 8
UNPROCESSABLE = 422
# GitHub ids and sizes of the review comment answers below.
THREAD_START, REPLY, APPROVAL, REVIEW = 1, 2, 9, 10
OPEN_THREAD_COMMENT = 3
LINE = 3
LONG_BODY = 2000
# Open issues a GitHub issue blocks, in its dependencies summary.
BLOCKING = 2


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

    def linked_fields(self) -> Dict[str, Any]:
        """The shape of a real `search/jql` issue with links: one open and one done blocker,
        one open ticket it blocks, a relates link, a due date and an epic parent."""
        blocks = {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"}
        relates = {"name": "Relates", "inward": "relates to", "outward": "relates to"}
        links = [
            {"type": blocks, "inwardIssue": {"key": "ENG-2", "fields": {"status": {"statusCategory": {"key": "indeterminate"}}}}},
            {"type": blocks, "inwardIssue": {"key": "ENG-3", "fields": {"status": {"statusCategory": {"key": "done"}}}}},
            {"type": blocks, "outwardIssue": {"key": "ENG-4", "fields": {"status": {"statusCategory": {"key": "new"}}}}},
            {"type": relates, "outwardIssue": {"key": "ENG-5", "fields": {"status": {"statusCategory": {"key": "new"}}}}},
        ]
        status = {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}
        return {"summary": "Fix login", "status": status, "issuelinks": links, "duedate": "2026-11-18", "parent": {"key": "ENG-100"}, "created": "2026-01-02"}


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
    def test_assigned_tickets_carry_the_signals_of_the_selector(self) -> None:
        """Spec 008 N3: open blockers, the open tickets it unblocks, due date, epic and
        progress, from the fields of a real Jira search answer (names replaced)."""
        client = JiraClient().client({})
        client.post.return_value = {"issues": [{"key": "ENG-1", "fields": JiraClient().linked_fields()}]}

        with mock.patch("atlassian.Jira", return_value=client):
            (ticket,) = make_tracker("jira", Logins().jira()).assigned("ENG", MAX_TICKETS)

        assert (ticket.open_blockers, ticket.unblocks, ticket.due_date, ticket.epic, ticket.in_progress) == (1, 1, "2026-11-18", "ENG-100", True)
        assert {"issuelinks", "duedate", "parent"} <= set(client.post.call_args.kwargs["data"]["fields"])

    def test_assigned_issues_carry_the_native_dependencies(self) -> None:
        rows = [{"number": ISSUE_NUMBER, "title": "Fix it", "state": "open", "issue_dependencies_summary": {"blocked_by": 1, "blocking": BLOCKING}}]

        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            (ticket,) = make_tracker("github-issues", Logins().github()).assigned("acme/app", "octo")

        assert (ticket.open_blockers, ticket.unblocks) == (1, BLOCKING)

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


class TestGithubReviews:
    """The verbs of the review routine (spec 003 PR 3: V3 to V6, V9)."""

    def test_pull_state_reads_open_merged_conflict_and_base(self) -> None:
        answer = {"state": "open", "merged": False, "mergeable_state": "dirty", "base": {"ref": "main"}}

        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=answer) as read:
            state = make_provider("github", Logins().github()).pull_state("acme/app", PULL_NUMBER)

        assert (state, read.call_args.args) == (PullState(True, False, True, "main"), ("/repos/acme/app/pulls/8",))

    def test_review_comments_keep_who_wrote_them_and_drop_empty_reviews(self) -> None:
        pages = {
            "/repos/acme/app/pulls/8/comments": [
                {"id": REPLY, "in_reply_to_id": THREAD_START, "user": {"login": "ann", "type": "User"}, "author_association": "MEMBER", "body": "x" * LONG_BODY, "path": "a.py", "line": LINE, "updated_at": "t2"}
            ],
            "/repos/acme/app/issues/8/comments": [{"id": REPLY, "user": {"login": "ci[bot]", "type": "Bot"}, "author_association": "NONE", "body": "coverage", "updated_at": "t3"}],
            "/repos/acme/app/pulls/8/reviews": [{"id": APPROVAL, "user": {"login": "bob"}, "body": "", "state": "APPROVED"}, {"id": REVIEW, "user": {"login": "bob"}, "body": "please split", "submitted_at": "t4"}],
        }  # fmt: skip

        with mock.patch("briar.extract._gh.GithubApi.get_paginated", side_effect=lambda path: pages[path]):
            found = make_provider("github", Logins().github()).review_comments("acme/app", PULL_NUMBER)

        assert [(comment.key(), comment.author, comment.association, comment.bot, comment.updated_at, comment.thread) for comment in found] == [
            ("inline:2", "ann", "MEMBER", False, "t2", "1"),
            ("conversation:2", "ci[bot]", "NONE", True, "t3", "2"),
            ("review:10", "bob", "", False, "t4", "10"),
        ]
        assert (len(found[0].body), found[0].file_path, found[0].line) == (LONG_BODY, "a.py", LINE)

    def test_resolved_comment_ids_come_from_the_resolved_threads(self) -> None:
        threads = [
            {"isResolved": True, "comments": {"nodes": [{"databaseId": THREAD_START}, {"databaseId": REPLY}]}},
            {"isResolved": False, "comments": {"nodes": [{"databaseId": OPEN_THREAD_COMMENT}]}},
        ]
        client = mock.MagicMock()
        client._Github__requester.graphql_query.return_value = ({}, {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}})

        with mock.patch("briar.extract._gh.GithubApi.client", return_value=client):
            found = make_provider("github", Logins().github()).resolved_comment_ids("acme/app", PULL_NUMBER)

        assert found == {"1", "2"}
        assert client._Github__requester.graphql_query.call_args.args[1] == {"owner": "acme", "name": "app", "number": PULL_NUMBER}

    def test_a_refused_thread_reply_is_a_login_rejected(self) -> None:
        from github import GithubException

        client = mock.MagicMock()
        client.get_repo.return_value.get_pull.return_value.create_review_comment_reply.side_effect = GithubException(UNPROCESSABLE, {"message": "No"}, None)

        with mock.patch("briar.extract._gh.GithubApi.client", return_value=client):
            with pytest.raises(LoginRejected, match=r"^HTTP 422: "):
                make_provider("github", Logins().github()).reply_in_thread("acme/app", ThreadReply(PULL_NUMBER, "1", "fixed"))

        assert client.get_repo.return_value.get_pull.return_value.create_review_comment_reply.call_args.args == (THREAD_START, "fixed")
