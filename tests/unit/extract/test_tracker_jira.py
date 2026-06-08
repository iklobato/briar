"""Boundary tests for `JiraTracker` (_trackers/jira.py).

Jira is reached via `atlassian-python-api`'s `Jira` client, lazily
built in `JiraTracker._jira()`. We mock at that seam — a fake client
whose `.post(...)` / `.issue(...)` return canned Jira REST payloads —
so no real client is constructed and no network happens. Assertions are
on the normalised `Ticket` / `Comment` dataclasses and the JQL/request
shape, not on mock bookkeeping.

To avoid constructing a real auth strategy, the tracker is built with
an injected fake `JiraAuthStrategy` (the `auth=` constructor arg wins
over autodetect).

Doc URLs modelled:
- JQL enhanced search (POST /rest/api/3/search/jql):
  https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-jql-post
  (response `{"issues": [{"key","self","fields":{...}}]}`)
- Issue fields (summary/status/issuetype/priority/reporter/assignee/labels):
  https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-get
- Comments (fields=comment → fields.comment.comments[]):
  https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/
- ADF description block:
  https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/
- Error envelope `{"errorMessages":[...], "errors":{...}}`:
  https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/#status-codes
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List
from unittest import mock

import pytest

from briar.extract._tracker import Comment, Ticket
from briar.extract._trackers._jira_auth import JiraAuthStrategy
from briar.extract._trackers.jira import JiraTracker


class _FakeAuth(JiraAuthStrategy):
    kind = "fake"

    @classmethod
    def required_env_vars(cls, *, company: str) -> List[str]:
        return []

    @classmethod
    def is_available(cls, *, company: str) -> bool:
        return True

    def configure(self, *, company: str, base_url: str) -> Dict[str, Any]:
        return {}


def _tracker():
    return JiraTracker(company="acme", auth=_FakeAuth())


def _issue(**over):
    # https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/
    issue = {
        "key": "PROJ-123",
        "self": "https://acme.atlassian.net/rest/api/3/issue/10001",
        "fields": {
            "summary": "Checkout fails for EU cards",
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "reporter": {"displayName": "Alice A"},
            "assignee": {"displayName": "Bob B"},
            "labels": ["payments", "eu"],
            "created": "2026-05-01T10:00:00.000+0000",
            "updated": "2026-05-02T11:00:00.000+0000",
        },
    }
    issue.update(over)
    return issue


# ---------------------------------------------------------------------------
# list_tickets
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListTicketsTests(unittest.TestCase):
    def test_parses_issues_into_tickets(self) -> None:
        client = mock.MagicMock()
        client.post.return_value = {"issues": [_issue(), _issue(key="PROJ-124")]}
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_tickets("PROJ", state="open", max_count=50)
        self.assertEqual(len(out), 2)
        t = out[0]
        self.assertIsInstance(t, Ticket)
        self.assertEqual(t.key, "PROJ-123")
        self.assertEqual(t.title, "Checkout fails for EU cards")
        self.assertEqual(t.reporter, "Alice A")
        self.assertEqual(t.assignee, "Bob B")
        self.assertEqual(t.status, "In Progress")
        self.assertEqual(t.kind, "Bug")
        self.assertEqual(t.priority, "High")
        self.assertEqual(t.labels, ["payments", "eu"])
        self.assertEqual(t.url, "https://acme.atlassian.net/rest/api/3/issue/10001")
        self.assertEqual(t.created_at, "2026-05-01T10:00:00.000+0000")

    def test_open_state_builds_not_done_jql(self) -> None:
        client = mock.MagicMock()
        client.post.return_value = {"issues": []}
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            _tracker().list_tickets("PROJ", state="open", max_count=50)
        endpoint, kwargs = client.post.call_args.args[0], client.post.call_args.kwargs
        self.assertEqual(endpoint, "rest/api/3/search/jql")
        jql = kwargs["data"]["jql"]
        self.assertIn('statusCategory != "Done"', jql)
        self.assertIn('project = "PROJ"', jql)

    def test_closed_state_builds_done_jql(self) -> None:
        client = mock.MagicMock()
        client.post.return_value = {"issues": []}
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            _tracker().list_tickets("PROJ", state="closed", max_count=50)
        jql = client.post.call_args.kwargs["data"]["jql"]
        self.assertIn('statusCategory = "Done"', jql)

    def test_maxresults_capped_at_100(self) -> None:
        client = mock.MagicMock()
        client.post.return_value = {"issues": []}
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            _tracker().list_tickets("PROJ", state="open", max_count=5000)
        self.assertEqual(client.post.call_args.kwargs["data"]["maxResults"], 100)

    def test_max_count_caps_returned_tickets(self) -> None:
        client = mock.MagicMock()
        client.post.return_value = {"issues": [_issue(key=f"PROJ-{n}") for n in range(10)]}
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_tickets("PROJ", state="open", max_count=3)
        self.assertEqual(len(out), 3)

    def test_empty_issues_returns_empty(self) -> None:
        client = mock.MagicMock()
        client.post.return_value = {"issues": []}
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_tickets("PROJ", state="open", max_count=50)
        self.assertEqual(out, [])

    def test_non_dict_result_returns_empty(self) -> None:
        # A malformed body (e.g. a string error page slipped past) must not blow up.
        client = mock.MagicMock()
        client.post.return_value = None
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_tickets("PROJ", state="open", max_count=50)
        self.assertEqual(out, [])

    def test_invalid_project_key_raises_value_error(self) -> None:
        # Boundary validation against JQL injection — @swallow_errors
        # deliberately re-raises ValueError rather than masking it.
        client = mock.MagicMock()
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            with self.assertRaises(ValueError):
                _tracker().list_tickets('PROJ" OR 1=1 --', state="open", max_count=50)
        client.post.assert_not_called()

    def test_api_exception_swallowed_to_empty(self) -> None:
        # A 401/403/5xx surfaces as a library exception; @swallow_errors
        # (default=[]) absorbs it. Models the Jira error envelope cause.
        client = mock.MagicMock()
        client.post.side_effect = RuntimeError('{"errorMessages":["Unauthorized"],"errors":{}}')
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_tickets("PROJ", state="open", max_count=50)
        self.assertEqual(out, [])

    def test_missing_nested_fields_default_to_empty(self) -> None:
        client = mock.MagicMock()
        client.post.return_value = {"issues": [{"key": "PROJ-9", "fields": {"summary": "bare"}}]}
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_tickets("PROJ", state="open", max_count=50)
        t = out[0]
        self.assertEqual(t.reporter, "")
        self.assertEqual(t.assignee, "")
        self.assertEqual(t.status, "")
        self.assertEqual(t.kind, "")
        self.assertEqual(t.priority, "")
        self.assertEqual(t.labels, [])


# ---------------------------------------------------------------------------
# list_comments
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListCommentsTests(unittest.TestCase):
    def test_parses_comments(self) -> None:
        # fields=comment → fields.comment.comments[]
        result = {
            "fields": {
                "comment": {
                    "comments": [
                        {"author": {"displayName": "Carol C"}, "body": "still repro", "created": "2026-05-03T00:00:00.000+0000"},
                    ]
                }
            }
        }
        client = mock.MagicMock()
        client.issue.return_value = result
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_comments("PROJ", "PROJ-123")
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], Comment)
        self.assertEqual(out[0].author, "Carol C")
        self.assertEqual(out[0].body, "still repro")
        self.assertEqual(out[0].created_at, "2026-05-03T00:00:00.000+0000")

    def test_adf_body_is_stringified(self) -> None:
        # Some comment bodies arrive as ADF dicts (not strings); the tracker
        # falls back to str() so the dataclass field stays a str.
        adf_body = {"type": "doc", "content": [{"type": "paragraph"}]}
        result = {"fields": {"comment": {"comments": [{"author": {}, "body": adf_body, "created": ""}]}}}
        client = mock.MagicMock()
        client.issue.return_value = result
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_comments("PROJ", "PROJ-123")
        self.assertIsInstance(out[0].body, str)
        self.assertIn("doc", out[0].body)

    def test_body_capped_at_500(self) -> None:
        result = {"fields": {"comment": {"comments": [{"author": {}, "body": "y" * 900, "created": ""}]}}}
        client = mock.MagicMock()
        client.issue.return_value = result
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_comments("PROJ", "PROJ-123")
        self.assertEqual(len(out[0].body), 500)

    def test_no_comments_returns_empty(self) -> None:
        client = mock.MagicMock()
        client.issue.return_value = {"fields": {"comment": {"comments": []}}}
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_comments("PROJ", "PROJ-123")
        self.assertEqual(out, [])

    def test_error_swallowed_to_empty(self) -> None:
        client = mock.MagicMock()
        client.issue.side_effect = RuntimeError("404 Issue does not exist")
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_comments("PROJ", "PROJ-999")
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# get_ticket + ADF flattening
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class GetTicketTests(unittest.TestCase):
    def test_flattens_adf_description(self) -> None:
        adf = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "First line."}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "Second line."}]},
            ],
        }
        issue = _issue()
        issue["fields"]["description"] = adf
        client = mock.MagicMock()
        client.issue.return_value = issue
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            t = _tracker().get_ticket("PROJ", "PROJ-123")
        self.assertEqual(t.key, "PROJ-123")
        self.assertEqual(t.description, "First line.\nSecond line.\n")

    def test_plain_string_description(self) -> None:
        issue = _issue()
        issue["fields"]["description"] = "just text"
        client = mock.MagicMock()
        client.issue.return_value = issue
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            t = _tracker().get_ticket("PROJ", "PROJ-123")
        self.assertEqual(t.description, "just text")

    def test_description_capped_at_8000(self) -> None:
        issue = _issue()
        issue["fields"]["description"] = "z" * 20000
        client = mock.MagicMock()
        client.issue.return_value = issue
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            t = _tracker().get_ticket("PROJ", "PROJ-123")
        self.assertEqual(len(t.description), 8000)

    def test_non_dict_issue_falls_back_to_super(self) -> None:
        client = mock.MagicMock()
        client.issue.return_value = None
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            t = _tracker().get_ticket("PROJ", "PROJ-123")
        self.assertEqual(t.key, "PROJ-123")
        self.assertEqual(t.title, "")

    def test_error_swallowed_to_none(self) -> None:
        client = mock.MagicMock()
        client.issue.side_effect = RuntimeError("500 internal")
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            t = _tracker().get_ticket("PROJ", "PROJ-123")
        self.assertIsNone(t)


# ---------------------------------------------------------------------------
# list_status_transitions (changelog)
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListStatusTransitionsTests(unittest.TestCase):
    def test_extracts_status_tostring_in_order(self) -> None:
        result = {
            "changelog": {
                "histories": [
                    {"items": [{"field": "status", "toString": "In Progress"}]},
                    {"items": [{"field": "assignee", "toString": "someone"}, {"field": "status", "toString": "In Review"}]},
                    {"items": [{"field": "status", "toString": "Done"}]},
                ]
            }
        }
        client = mock.MagicMock()
        client.issue.return_value = result
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_status_transitions("PROJ", "PROJ-123")
        self.assertEqual(out, ["In Progress", "In Review", "Done"])

    def test_empty_changelog_returns_empty(self) -> None:
        client = mock.MagicMock()
        client.issue.return_value = {"changelog": {"histories": []}}
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_status_transitions("PROJ", "PROJ-123")
        self.assertEqual(out, [])

    def test_error_swallowed_to_empty(self) -> None:
        client = mock.MagicMock()
        client.issue.side_effect = RuntimeError("boom")
        with mock.patch.object(JiraTracker, "_jira", return_value=client):
            out = _tracker().list_status_transitions("PROJ", "PROJ-123")
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# ADF flattening unit + availability
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class AdfAndAvailabilityTests(unittest.TestCase):
    def test_adf_to_text_block_newlines_and_lists(self) -> None:
        doc = {
            "type": "doc",
            "content": [
                {"type": "heading", "content": [{"type": "text", "text": "Title"}]},
                {
                    "type": "bulletList",
                    "content": [
                        {"type": "listItem", "content": [{"type": "text", "text": "one"}]},
                        {"type": "listItem", "content": [{"type": "text", "text": "two"}]},
                    ],
                },
            ],
        }
        out = JiraTracker._adf_to_text(doc)
        self.assertIn("Title", out)
        self.assertIn("one", out)
        self.assertIn("two", out)
        # heading is a block node → trailing newline emitted.
        self.assertTrue(out.startswith("Title\n"))

    def test_is_available_requires_url_and_auth(self) -> None:
        with mock.patch.dict("os.environ", {"JIRA_ACME_URL": "https://acme.atlassian.net"}):
            self.assertTrue(JiraTracker(company="acme", auth=_FakeAuth()).is_available())

    def test_is_available_false_without_url(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(JiraTracker(company="acme", auth=_FakeAuth()).is_available())

    def test_jira_client_built_lazily_with_auth_kwargs(self) -> None:
        # `_jira()` splats the auth strategy's configure() kwargs into
        # atlassian.Jira(url=..., cloud=True, timeout=30, **kwargs) and caches it.
        class _KwAuth(_FakeAuth):
            def configure(self, *, company, base_url):
                return {"username": "e@x.com", "password": "tok"}

        with mock.patch.dict("os.environ", {"JIRA_ACME_URL": "https://acme.atlassian.net"}):
            tracker = JiraTracker(company="acme", auth=_KwAuth())
            with mock.patch("atlassian.Jira") as jira_cls:
                jira_cls.return_value = "client-instance"
                first = tracker._jira()
                second = tracker._jira()  # cached — no second construction
        self.assertEqual(first, "client-instance")
        self.assertIs(first, second)
        jira_cls.assert_called_once()
        _, kwargs = jira_cls.call_args
        self.assertEqual(kwargs["url"], "https://acme.atlassian.net")
        self.assertTrue(kwargs["cloud"])
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["username"], "e@x.com")
        self.assertEqual(kwargs["password"], "tok")


if __name__ == "__main__":
    unittest.main()
