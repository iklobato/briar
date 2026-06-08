"""Boundary tests for `GithubIssuesTracker` (_trackers/github_issues.py).

The tracker reaches GitHub only through the `GithubApi` facade
(`get_paginated` / `get_json`), so we mock at that seam — the same
convention the existing `tests/test_extract.py` uses for the GitHub
provider. We assert the NORMALISED `Ticket` / `Comment` dataclasses
(fields, types, counts), not that a mock was called.

Doc URLs modelled (real fields + error envelope):
- List issues: https://docs.github.com/en/rest/issues/issues#list-repository-issues
  (each row has number/title/state/user/assignee/labels/created_at/html_url;
  PRs appear in the same feed and carry a `pull_request` key)
- Issue comments: https://docs.github.com/en/rest/issues/comments
- Issue events (timeline): https://docs.github.com/en/rest/issues/events
- Error envelope: https://docs.github.com/en/rest — `{"message", "documentation_url"}`
  surfaced as `github.GithubException`. The verbs are wrapped in
  `@swallow_errors(default=[]/None)`, so a raised GithubException becomes
  an empty/None result (NOT a propagated exception).
"""

from __future__ import annotations

import unittest
from unittest import mock

import pytest
from github import GithubException

from briar.extract._tracker import Comment, Ticket
from briar.extract._trackers.github_issues import GithubIssuesTracker, _kind_priority_from_labels


def _issue_row(**over):
    # Models https://docs.github.com/en/rest/issues/issues#list-repository-issues
    row = {
        "number": 42,
        "title": "Login breaks on Safari",
        "state": "open",
        "user": {"login": "alice"},
        "assignee": {"login": "bob"},
        "labels": [{"name": "bug"}, {"name": "priority/high"}],
        "created_at": "2026-05-01T10:00:00Z",
        "updated_at": "2026-05-02T11:00:00Z",
        "html_url": "https://github.com/acme/app/issues/42",
        "body": "Steps to reproduce...",
    }
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# list_tickets — normal fetch
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListTicketsTests(unittest.TestCase):
    def test_parses_rows_into_tickets(self) -> None:
        rows = [_issue_row(), _issue_row(number=43, title="Second", labels=[])]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubIssuesTracker().list_tickets("acme/app", state="open", max_count=10)
        self.assertEqual(len(out), 2)
        t = out[0]
        self.assertIsInstance(t, Ticket)
        self.assertEqual(t.key, "#42")
        self.assertEqual(t.title, "Login breaks on Safari")
        self.assertEqual(t.reporter, "alice")
        self.assertEqual(t.assignee, "bob")
        self.assertEqual(t.status, "open")
        self.assertEqual(t.kind, "bug")  # derived from labels
        self.assertEqual(t.priority, "priority/high")
        self.assertEqual(t.labels, ["bug", "priority/high"])
        self.assertEqual(t.url, "https://github.com/acme/app/issues/42")
        self.assertEqual(t.created_at, "2026-05-01T10:00:00Z")
        self.assertEqual(t.updated_at, "2026-05-02T11:00:00Z")

    def test_closed_state_requests_closed_query(self) -> None:
        captured = {}

        def fake(path, max_pages=50):
            captured["path"] = path
            return []

        with mock.patch("briar.extract._gh.GithubApi.get_paginated", side_effect=fake):
            GithubIssuesTracker().list_tickets("acme/app", state="closed", max_count=5)
        self.assertIn("state=closed", captured["path"])

    def test_open_state_requests_open_query(self) -> None:
        captured = {}

        def fake(path, max_pages=50):
            captured["path"] = path
            return []

        with mock.patch("briar.extract._gh.GithubApi.get_paginated", side_effect=fake):
            GithubIssuesTracker().list_tickets("acme/app", state="open", max_count=5)
        self.assertIn("state=open", captured["path"])

    def test_pull_requests_are_filtered_out(self) -> None:
        # GitHub's /issues feed mixes in PRs (they carry a `pull_request` key).
        rows = [
            _issue_row(number=1),
            _issue_row(number=2, pull_request={"url": "https://api.github.com/.../pulls/2"}),
            _issue_row(number=3),
        ]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubIssuesTracker().list_tickets("acme/app", state="open", max_count=10)
        self.assertEqual([t.key for t in out], ["#1", "#3"])

    def test_max_count_caps_result(self) -> None:
        rows = [_issue_row(number=n) for n in range(1, 11)]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubIssuesTracker().list_tickets("acme/app", state="open", max_count=3)
        self.assertEqual(len(out), 3)

    def test_empty_feed_returns_empty_list(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=[]):
            out = GithubIssuesTracker().list_tickets("acme/app", state="open", max_count=10)
        self.assertEqual(out, [])

    def test_string_labels_are_tolerated(self) -> None:
        # Defensive: labels can arrive as bare strings in some payloads.
        rows = [_issue_row(labels=["bug", "wontfix"])]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubIssuesTracker().list_tickets("acme/app", state="open", max_count=10)
        self.assertEqual(out[0].labels, ["bug", "wontfix"])
        self.assertEqual(out[0].kind, "bug")

    def test_missing_user_and_assignee_default_to_empty(self) -> None:
        rows = [_issue_row(user=None, assignee=None)]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubIssuesTracker().list_tickets("acme/app", state="open", max_count=10)
        self.assertEqual(out[0].reporter, "")
        self.assertEqual(out[0].assignee, "")

    def test_title_capped_at_200_chars(self) -> None:
        rows = [_issue_row(title="x" * 500)]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubIssuesTracker().list_tickets("acme/app", state="open", max_count=10)
        self.assertEqual(len(out[0].title), 200)

    def test_github_exception_is_swallowed_to_empty_list(self) -> None:
        # 403 rate-limit / 404 etc → @swallow_errors(default=[]) returns [].
        err = GithubException(403, {"message": "API rate limit exceeded"}, {"x-ratelimit-remaining": "0"})
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", side_effect=err):
            out = GithubIssuesTracker().list_tickets("acme/app", state="open", max_count=10)
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# list_comments
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListCommentsTests(unittest.TestCase):
    def test_parses_comments(self) -> None:
        # https://docs.github.com/en/rest/issues/comments
        rows = [
            {"user": {"login": "carol"}, "body": "Confirmed on my end", "created_at": "2026-05-03T00:00:00Z"},
            {"user": {"login": "dan"}, "body": "Fixed in #44", "created_at": "2026-05-04T00:00:00Z"},
        ]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubIssuesTracker().list_comments("acme/app", "#42")
        self.assertEqual(len(out), 2)
        self.assertIsInstance(out[0], Comment)
        self.assertEqual(out[0].author, "carol")
        self.assertEqual(out[0].body, "Confirmed on my end")
        self.assertEqual(out[0].created_at, "2026-05-03T00:00:00Z")

    def test_ticket_key_with_repo_prefix_extracts_number(self) -> None:
        captured = {}

        def fake(path, max_pages=2):
            captured["path"] = path
            return []

        with mock.patch("briar.extract._gh.GithubApi.get_paginated", side_effect=fake):
            GithubIssuesTracker().list_comments("acme/app", "acme/app#99")
        self.assertIn("/issues/99/comments", captured["path"])

    def test_body_capped_at_500(self) -> None:
        rows = [{"user": {"login": "x"}, "body": "y" * 800, "created_at": ""}]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=rows):
            out = GithubIssuesTracker().list_comments("acme/app", "42")
        self.assertEqual(len(out[0].body), 500)

    def test_swallows_error_to_empty(self) -> None:
        err = GithubException(404, {"message": "Not Found"}, {})
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", side_effect=err):
            out = GithubIssuesTracker().list_comments("acme/app", "42")
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# get_ticket
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class GetTicketTests(unittest.TestCase):
    def test_populates_description_from_body(self) -> None:
        data = _issue_row(body="## Repro\nclick login")
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=data):
            t = GithubIssuesTracker().get_ticket("acme/app", "#42")
        self.assertEqual(t.key, "#42")
        self.assertIn("click login", t.description)

    def test_description_capped_at_8000(self) -> None:
        data = _issue_row(body="z" * 20000)
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=data):
            t = GithubIssuesTracker().get_ticket("acme/app", "#42")
        self.assertEqual(len(t.description), 8000)

    def test_non_dict_response_falls_back_to_super(self) -> None:
        # When the facade returns a non-dict (malformed), get_ticket falls
        # back to the ABC's empty Ticket keyed by ticket_key.
        with mock.patch("briar.extract._gh.GithubApi.get_json", return_value=None):
            t = GithubIssuesTracker().get_ticket("acme/app", "#42")
        self.assertEqual(t.key, "#42")
        self.assertEqual(t.title, "")

    def test_swallows_error_to_none(self) -> None:
        err = GithubException(500, {"message": "Server Error"}, {})
        with mock.patch("briar.extract._gh.GithubApi.get_json", side_effect=err):
            t = GithubIssuesTracker().get_ticket("acme/app", "#42")
        self.assertIsNone(t)


# ---------------------------------------------------------------------------
# list_status_transitions
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListStatusTransitionsTests(unittest.TestCase):
    def test_filters_to_closed_and_reopened_events(self) -> None:
        # https://docs.github.com/en/rest/issues/events
        events = [
            {"event": "labeled"},
            {"event": "closed"},
            {"event": "reopened"},
            {"event": "assigned"},
            {"event": "closed"},
        ]
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=events):
            out = GithubIssuesTracker().list_status_transitions("acme/app", "#42")
        self.assertEqual(out, ["closed", "reopened", "closed"])

    def test_empty_events_returns_empty(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", return_value=[]):
            out = GithubIssuesTracker().list_status_transitions("acme/app", "#42")
        self.assertEqual(out, [])

    def test_swallows_error_to_empty(self) -> None:
        with mock.patch("briar.extract._gh.GithubApi.get_paginated", side_effect=GithubException(403, {}, {})):
            out = GithubIssuesTracker().list_status_transitions("acme/app", "#42")
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# is_available / required_env_vars / label derivation
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class AvailabilityAndLabelTests(unittest.TestCase):
    def test_is_available_true_with_token(self) -> None:
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_x"}):
            self.assertTrue(GithubIssuesTracker().is_available())

    def test_is_available_false_without_token(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(GithubIssuesTracker().is_available())

    def test_required_env_vars(self) -> None:
        self.assertEqual(GithubIssuesTracker.required_env_vars("acme"), ["GITHUB_TOKEN"])

    def test_kind_priority_first_match_wins(self) -> None:
        kind, priority = _kind_priority_from_labels(["story", "bug", "P1", "priority/low"])
        self.assertEqual(kind, "story")  # first kind label wins
        self.assertEqual(priority, "P1")  # first priority-prefixed label wins

    def test_kind_priority_none_match(self) -> None:
        self.assertEqual(_kind_priority_from_labels(["needs-triage", "ui"]), ("", ""))


if __name__ == "__main__":
    unittest.main()
