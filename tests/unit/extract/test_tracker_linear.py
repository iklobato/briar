"""Boundary tests for `LinearTracker` (_trackers/linear.py).

Linear is a GraphQL API hit via stdlib `urllib` through the project's
`urlopen_with_retry` helper. The tracker imports that helper into its
own namespace, so we patch `briar.extract._trackers.linear.urlopen_with_retry`
and hand back a fake response context manager whose `.read()` returns
the JSON bytes — this also lets us assert the EXACT request shape
(endpoint, method, the no-`Bearer` Authorization header, GraphQL body).

Doc URLs modelled:
- GraphQL envelope `{"data": {...}}` / `{"errors": [...]}`:
  https://developers.linear.app/docs/graphql/working-with-the-graphql-api
- Issue / IssueConnection fields (identifier, title, state.type, etc.):
  https://developers.linear.app/docs/graphql/working-with-the-graphql-api
- Auth header is the raw API key, NO `Bearer` prefix (Linear quirk):
  https://developers.linear.app/docs/graphql/working-with-the-graphql-api#authentication
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

import pytest

from briar.extract._tracker import Comment, Ticket
from briar.extract._trackers.linear import LinearTracker


class _FakeResp:
    def __init__(self, body: dict):
        self._bytes = json.dumps(body).encode("utf-8")

    def read(self):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_gql(body=None, *, capture=None, side_effect=None):
    """Patch urlopen_with_retry. If `capture` (a dict) is given, record
    the urllib Request passed to the helper for request-shape assertions."""

    def fake(req, *, timeout):
        if capture is not None:
            capture["req"] = req
        if side_effect is not None:
            raise side_effect
        return _FakeResp(body)

    return mock.patch("briar.extract._trackers.linear.urlopen_with_retry", side_effect=fake)


def _tracker(token="lin_api_key"):
    with mock.patch.dict("os.environ", {"LINEAR_ACME_TOKEN": token}):
        return LinearTracker(company="acme")


# ---------------------------------------------------------------------------
# list_tickets
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListTicketsTests(unittest.TestCase):
    def _nodes_body(self):
        # Models the IssueConnection.nodes shape from the Linear GraphQL schema.
        return {
            "data": {
                "issues": {
                    "nodes": [
                        {
                            "identifier": "ENG-7",
                            "title": "Flaky deploy",
                            "createdAt": "2026-05-01T00:00:00.000Z",
                            "updatedAt": "2026-05-02T00:00:00.000Z",
                            "url": "https://linear.app/acme/issue/ENG-7",
                            "priorityLabel": "High",
                            "state": {"name": "In Progress", "type": "started"},
                            "creator": {"displayName": "Alice A", "name": "alice"},
                            "assignee": {"displayName": "Bob B", "name": "bob"},
                            "labels": {"nodes": [{"name": "infra"}, {"name": "urgent"}]},
                        }
                    ]
                }
            }
        }

    def test_parses_nodes_into_tickets(self) -> None:
        with _patch_gql(self._nodes_body()):
            out = _tracker().list_tickets("ENG", state="open", max_count=25)
        self.assertEqual(len(out), 1)
        t = out[0]
        self.assertIsInstance(t, Ticket)
        self.assertEqual(t.key, "ENG-7")
        self.assertEqual(t.title, "Flaky deploy")
        self.assertEqual(t.reporter, "Alice A")  # displayName preferred over name
        self.assertEqual(t.assignee, "Bob B")
        self.assertEqual(t.status, "In Progress")
        self.assertEqual(t.kind, "")  # Linear has no native issue-type
        self.assertEqual(t.priority, "High")
        self.assertEqual(t.labels, ["infra", "urgent"])
        self.assertEqual(t.url, "https://linear.app/acme/issue/ENG-7")

    def test_request_shape_endpoint_method_auth_and_variables(self) -> None:
        capture: dict = {}
        with _patch_gql(self._nodes_body(), capture=capture):
            _tracker("lin_secret").list_tickets("ENG", state="closed", max_count=25)
        req = capture["req"]
        self.assertEqual(req.full_url, "https://api.linear.app/graphql")
        self.assertEqual(req.get_method(), "POST")
        # NO "Bearer " prefix — Linear-specific. urllib title-cases header keys.
        self.assertEqual(req.get_header("Authorization"), "lin_secret")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["variables"]["team"], "ENG")
        self.assertEqual(payload["variables"]["first"], 25)
        # state=closed → completed/cancelled state types.
        self.assertEqual(payload["variables"]["stateTypes"], ["completed", "cancelled"])

    def test_open_state_uses_open_state_types(self) -> None:
        capture: dict = {}
        with _patch_gql(self._nodes_body(), capture=capture):
            _tracker().list_tickets("ENG", state="open", max_count=5)
        payload = json.loads(capture["req"].data.decode("utf-8"))
        self.assertEqual(payload["variables"]["stateTypes"], ["triage", "backlog", "unstarted", "started"])

    def test_empty_nodes_returns_empty(self) -> None:
        with _patch_gql({"data": {"issues": {"nodes": []}}}):
            out = _tracker().list_tickets("ENG", state="open", max_count=5)
        self.assertEqual(out, [])

    def test_missing_data_key_returns_empty(self) -> None:
        with _patch_gql({"data": None}):
            out = _tracker().list_tickets("ENG", state="open", max_count=5)
        self.assertEqual(out, [])

    def test_graphql_errors_swallowed_to_empty(self) -> None:
        # _gql raises RuntimeError on `errors`; @swallow_errors(default=[])
        # turns that into []. Models the documented GraphQL error envelope.
        body = {"errors": [{"message": "Authentication required, not authenticated", "extensions": {"code": "AUTHENTICATION_ERROR"}}]}
        with _patch_gql(body):
            out = _tracker().list_tickets("ENG", state="open", max_count=5)
        self.assertEqual(out, [])

    def test_creator_falls_back_to_name_when_no_display_name(self) -> None:
        body = {
            "data": {
                "issues": {
                    "nodes": [
                        {
                            "identifier": "ENG-1",
                            "title": "x",
                            "creator": {"name": "raw_login"},
                            "assignee": None,
                            "state": {"name": "Todo", "type": "unstarted"},
                            "labels": {"nodes": []},
                        }
                    ]
                }
            }
        }
        with _patch_gql(body):
            out = _tracker().list_tickets("ENG", state="open", max_count=5)
        self.assertEqual(out[0].reporter, "raw_login")
        self.assertEqual(out[0].assignee, "")


# ---------------------------------------------------------------------------
# list_comments
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListCommentsTests(unittest.TestCase):
    def test_parses_comments(self) -> None:
        body = {
            "data": {
                "issue": {
                    "comments": {
                        "nodes": [
                            {"body": "looks good", "createdAt": "2026-05-03T00:00:00.000Z", "user": {"displayName": "Carol C", "name": "carol"}},
                        ]
                    }
                }
            }
        }
        with _patch_gql(body):
            out = _tracker().list_comments("ENG", "ENG-7")
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], Comment)
        self.assertEqual(out[0].author, "Carol C")
        self.assertEqual(out[0].body, "looks good")
        self.assertEqual(out[0].created_at, "2026-05-03T00:00:00.000Z")

    def test_missing_issue_returns_empty(self) -> None:
        with _patch_gql({"data": {"issue": None}}):
            out = _tracker().list_comments("ENG", "ENG-7")
        self.assertEqual(out, [])

    def test_graphql_errors_swallowed_to_empty(self) -> None:
        with _patch_gql({"errors": [{"message": "Entity not found"}]}):
            out = _tracker().list_comments("ENG", "ENG-404")
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# get_ticket
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class GetTicketTests(unittest.TestCase):
    def _issue_body(self, description="full body here"):
        return {
            "data": {
                "issue": {
                    "identifier": "ENG-7",
                    "title": "Flaky deploy",
                    "description": description,
                    "createdAt": "2026-05-01T00:00:00.000Z",
                    "updatedAt": "2026-05-02T00:00:00.000Z",
                    "url": "https://linear.app/acme/issue/ENG-7",
                    "priorityLabel": "High",
                    "state": {"name": "In Progress", "type": "started"},
                    "creator": {"displayName": "Alice A"},
                    "assignee": {"displayName": "Bob B"},
                    "labels": {"nodes": [{"name": "infra"}]},
                }
            }
        }

    def test_populates_description(self) -> None:
        with _patch_gql(self._issue_body("## Repro\nrun ci")):
            t = _tracker().get_ticket("ENG", "ENG-7")
        self.assertEqual(t.key, "ENG-7")
        self.assertIn("run ci", t.description)

    def test_description_capped_at_8000(self) -> None:
        with _patch_gql(self._issue_body("z" * 20000)):
            t = _tracker().get_ticket("ENG", "ENG-7")
        self.assertEqual(len(t.description), 8000)

    def test_missing_issue_falls_back_to_super(self) -> None:
        with _patch_gql({"data": {"issue": None}}):
            t = _tracker().get_ticket("ENG", "ENG-7")
        self.assertEqual(t.key, "ENG-7")
        self.assertEqual(t.title, "")

    def test_graphql_errors_swallowed_to_none(self) -> None:
        with _patch_gql({"errors": [{"message": "boom"}]}):
            t = _tracker().get_ticket("ENG", "ENG-7")
        self.assertIsNone(t)


# ---------------------------------------------------------------------------
# availability + transport error handling
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class AvailabilityAndTransportTests(unittest.TestCase):
    def test_is_available_true_with_token(self) -> None:
        self.assertTrue(_tracker().is_available())

    def test_is_available_false_without_token(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(LinearTracker(company="acme").is_available())

    def test_required_env_vars(self) -> None:
        self.assertEqual(LinearTracker.required_env_vars("acme"), ["LINEAR_ACME_TOKEN"])

    def test_required_env_vars_empty_company(self) -> None:
        self.assertEqual(LinearTracker.required_env_vars(""), [])

    def test_transport_error_swallowed_to_empty(self) -> None:
        # urlopen_with_retry re-raises after exhausting retries; the public
        # verb's @swallow_errors(default=[]) absorbs it.
        import urllib.error

        err = urllib.error.URLError("connection refused")
        with _patch_gql(side_effect=err):
            out = _tracker().list_tickets("ENG", state="open", max_count=5)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
