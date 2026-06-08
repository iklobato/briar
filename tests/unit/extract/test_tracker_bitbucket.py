"""Boundary tests for `BitbucketIssuesTracker` (_trackers/bitbucket.py).

Bitbucket Cloud issues are reached through `atlassian-python-api`'s
Cloud client:
``self._cloud().workspaces.get(ws).repositories.get(slug).get(<path>, params=...)``.
We mock at `_cloud()` with a fake client whose `.get(...)` returns
canned Bitbucket v2 envelopes, so no real client / network. Assertions
are on the normalised `Ticket` / `Comment` dataclasses and the request
params, not mock bookkeeping.

Doc URLs modelled:
- List issues (paginated `{"values":[...], "page", "pagelen", "next"}`):
  https://developer.atlassian.com/cloud/bitbucket/rest/api-group-issue-tracker/#api-repositories-workspace-repo-slug-issues-get
  (issue fields: id/title/state/kind/priority/reporter/assignee/created_on/updated_on/content/links)
- Issue comments:
  https://developer.atlassian.com/cloud/bitbucket/rest/api-group-issue-tracker/#api-repositories-workspace-repo-slug-issues-issue-id-comments-get
- Error envelope `{"type":"error","error":{"message":...}}`:
  https://developer.atlassian.com/cloud/bitbucket/rest/intro/#error-response
"""

from __future__ import annotations

import unittest
from unittest import mock

import pytest

from briar.extract._tracker import Comment, Ticket
from briar.extract._trackers.bitbucket import BitbucketIssuesTracker


class _FakeRepo:
    """Stand-in for the Cloud repository object. Records `.get` calls and
    replays canned responses keyed by a substring of the request path."""

    def __init__(self, responses, *, raise_on_get=None):
        # responses: list of (path_substr, value) checked in order.
        self._responses = responses
        self._raise = raise_on_get
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if self._raise is not None:
            raise self._raise
        for substr, value in self._responses:
            if substr in path:
                return value
        raise AssertionError(f"unexpected path {path!r}")


def _cloud_returning(repo):
    """Patch `_cloud()` so the workspaces→repositories chain yields `repo`."""
    cloud = mock.MagicMock()
    cloud.workspaces.get.return_value.repositories.get.return_value = repo
    return mock.patch.object(BitbucketIssuesTracker, "_cloud", return_value=cloud)


def _tracker():
    # Bypass CredEnv reads; the tracker only needs a constructed instance
    # since we patch _cloud() directly.
    return BitbucketIssuesTracker(company="acme")


def _issue(**over):
    issue = {
        "id": 5,
        "title": "Crash on export",
        "state": "open",
        "kind": "bug",
        "priority": "major",
        "reporter": {"display_name": "Alice A"},
        "assignee": {"display_name": "Bob B"},
        "created_on": "2026-05-01T10:00:00.000000+00:00",
        "updated_on": "2026-05-02T11:00:00.000000+00:00",
        "content": {"raw": "stack trace here"},
        "links": {"html": {"href": "https://bitbucket.org/acme/app/issues/5"}},
    }
    issue.update(over)
    return issue


# ---------------------------------------------------------------------------
# list_tickets
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListTicketsTests(unittest.TestCase):
    def test_parses_issues_into_tickets(self) -> None:
        repo = _FakeRepo([("issues", {"values": [_issue(), _issue(id=6, title="Second")]})])
        with _cloud_returning(repo):
            out = _tracker().list_tickets("acme/app", state="open", max_count=50)
        self.assertEqual(len(out), 2)
        t = out[0]
        self.assertIsInstance(t, Ticket)
        self.assertEqual(t.key, "#5")
        self.assertEqual(t.title, "Crash on export")
        self.assertEqual(t.reporter, "Alice A")
        self.assertEqual(t.assignee, "Bob B")
        self.assertEqual(t.status, "open")
        self.assertEqual(t.kind, "bug")
        self.assertEqual(t.priority, "major")
        self.assertEqual(t.labels, [])  # BB issues have no labels
        self.assertEqual(t.url, "https://bitbucket.org/acme/app/issues/5")
        self.assertEqual(t.created_at, "2026-05-01T10:00:00.000000+00:00")

    def test_open_state_query_maps_to_new_open_onhold(self) -> None:
        repo = _FakeRepo([("issues", {"values": []})])
        with _cloud_returning(repo):
            _tracker().list_tickets("acme/app", state="open", max_count=50)
        _, params = repo.calls[0]
        self.assertIn('state="new"', params["q"])
        self.assertIn('state="open"', params["q"])
        self.assertIn('state="on hold"', params["q"])

    def test_closed_state_query_maps_to_resolved_set(self) -> None:
        repo = _FakeRepo([("issues", {"values": []})])
        with _cloud_returning(repo):
            _tracker().list_tickets("acme/app", state="closed", max_count=50)
        _, params = repo.calls[0]
        self.assertIn('state="resolved"', params["q"])
        self.assertIn('state="closed"', params["q"])
        self.assertNotIn('state="new"', params["q"])

    def test_pagelen_capped_at_100(self) -> None:
        repo = _FakeRepo([("issues", {"values": []})])
        with _cloud_returning(repo):
            _tracker().list_tickets("acme/app", state="open", max_count=5000)
        _, params = repo.calls[0]
        self.assertEqual(params["pagelen"], 100)

    def test_max_count_caps_returned(self) -> None:
        repo = _FakeRepo([("issues", {"values": [_issue(id=n) for n in range(10)]})])
        with _cloud_returning(repo):
            out = _tracker().list_tickets("acme/app", state="open", max_count=3)
        self.assertEqual(len(out), 3)

    def test_empty_values_returns_empty(self) -> None:
        repo = _FakeRepo([("issues", {"values": []})])
        with _cloud_returning(repo):
            out = _tracker().list_tickets("acme/app", state="open", max_count=50)
        self.assertEqual(out, [])

    def test_non_dict_envelope_returns_empty(self) -> None:
        repo = _FakeRepo([("issues", None)])
        with _cloud_returning(repo):
            out = _tracker().list_tickets("acme/app", state="open", max_count=50)
        self.assertEqual(out, [])

    def test_bare_project_uses_configured_workspace(self) -> None:
        repo = _FakeRepo([("issues", {"values": []})])
        cloud = mock.MagicMock()
        cloud.workspaces.get.return_value.repositories.get.return_value = repo
        with mock.patch.object(BitbucketIssuesTracker, "_cloud", return_value=cloud):
            tracker = _tracker()
            tracker._workspace_slug = "myws"
            tracker.list_tickets("just-repo", state="open", max_count=5)
        cloud.workspaces.get.assert_called_once_with("myws")
        cloud.workspaces.get.return_value.repositories.get.assert_called_once_with("just-repo")

    def test_api_exception_swallowed_to_empty(self) -> None:
        # 401/403/404/5xx surfaces as a library exception → swallowed to [].
        repo = _FakeRepo([], raise_on_get=RuntimeError('{"type":"error","error":{"message":"Unauthorized"}}'))
        with _cloud_returning(repo):
            out = _tracker().list_tickets("acme/app", state="open", max_count=50)
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# list_comments
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class ListCommentsTests(unittest.TestCase):
    def test_parses_comments(self) -> None:
        envelope = {
            "values": [
                {"user": {"display_name": "Carol C"}, "content": {"raw": "still broken"}, "created_on": "2026-05-03T00:00:00+00:00"},
            ]
        }
        repo = _FakeRepo([("comments", envelope)])
        with _cloud_returning(repo):
            out = _tracker().list_comments("acme/app", "#5")
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], Comment)
        self.assertEqual(out[0].author, "Carol C")
        self.assertEqual(out[0].body, "still broken")
        self.assertEqual(out[0].created_at, "2026-05-03T00:00:00+00:00")

    def test_issue_id_strips_hash(self) -> None:
        repo = _FakeRepo([("comments", {"values": []})])
        with _cloud_returning(repo):
            _tracker().list_comments("acme/app", "#5")
        path, _ = repo.calls[0]
        self.assertEqual(path, "issues/5/comments")

    def test_body_capped_at_500(self) -> None:
        envelope = {"values": [{"user": {}, "content": {"raw": "y" * 900}, "created_on": ""}]}
        repo = _FakeRepo([("comments", envelope)])
        with _cloud_returning(repo):
            out = _tracker().list_comments("acme/app", "#5")
        self.assertEqual(len(out[0].body), 500)

    def test_error_swallowed_to_empty(self) -> None:
        repo = _FakeRepo([], raise_on_get=RuntimeError("404 not found"))
        with _cloud_returning(repo):
            out = _tracker().list_comments("acme/app", "#5")
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# get_ticket
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class GetTicketTests(unittest.TestCase):
    def test_populates_description_from_content_raw(self) -> None:
        repo = _FakeRepo([("issues/5", _issue(content={"raw": "## Repro\nclick export"}))])
        with _cloud_returning(repo):
            t = _tracker().get_ticket("acme/app", "#5")
        self.assertEqual(t.key, "#5")
        self.assertIn("click export", t.description)

    def test_description_capped_at_8000(self) -> None:
        repo = _FakeRepo([("issues/5", _issue(content={"raw": "z" * 20000}))])
        with _cloud_returning(repo):
            t = _tracker().get_ticket("acme/app", "#5")
        self.assertEqual(len(t.description), 8000)

    def test_non_dict_falls_back_to_super(self) -> None:
        repo = _FakeRepo([("issues/5", None)])
        with _cloud_returning(repo):
            t = _tracker().get_ticket("acme/app", "#5")
        self.assertEqual(t.key, "#5")
        self.assertEqual(t.title, "")

    def test_error_swallowed_to_none(self) -> None:
        repo = _FakeRepo([], raise_on_get=RuntimeError("500"))
        with _cloud_returning(repo):
            t = _tracker().get_ticket("acme/app", "#5")
        self.assertIsNone(t)


# ---------------------------------------------------------------------------
# addr resolution + availability
# ---------------------------------------------------------------------------


@pytest.mark.boundary
class AddrAndAvailabilityTests(unittest.TestCase):
    def test_resolve_addr_with_slash(self) -> None:
        self.assertEqual(_tracker()._resolve_addr("ws/repo"), ("ws", "repo"))

    def test_resolve_addr_bare_uses_workspace(self) -> None:
        t = _tracker()
        t._workspace_slug = "myws"
        self.assertEqual(t._resolve_addr("repo"), ("myws", "repo"))

    def test_is_available_requires_all_three_creds(self) -> None:
        env = {"BITBUCKET_ACME_USERNAME": "u", "BITBUCKET_ACME_APP_PASSWORD": "p", "BITBUCKET_ACME_WORKSPACE": "w"}
        with mock.patch.dict("os.environ", env):
            self.assertTrue(BitbucketIssuesTracker(company="acme").is_available())

    def test_is_available_false_when_missing_workspace(self) -> None:
        env = {"BITBUCKET_ACME_USERNAME": "u", "BITBUCKET_ACME_APP_PASSWORD": "p"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertFalse(BitbucketIssuesTracker(company="acme").is_available())

    def test_required_env_vars(self) -> None:
        self.assertEqual(
            BitbucketIssuesTracker.required_env_vars("acme"),
            ["BITBUCKET_ACME_USERNAME", "BITBUCKET_ACME_APP_PASSWORD", "BITBUCKET_ACME_WORKSPACE"],
        )

    def test_cloud_client_built_lazily_with_basic_auth(self) -> None:
        env = {"BITBUCKET_ACME_USERNAME": "u", "BITBUCKET_ACME_APP_PASSWORD": "p", "BITBUCKET_ACME_WORKSPACE": "w"}
        with mock.patch.dict("os.environ", env):
            tracker = BitbucketIssuesTracker(company="acme")
            with mock.patch("atlassian.bitbucket.cloud.Cloud") as cloud_cls:
                cloud_cls.return_value = "cloud-instance"
                first = tracker._cloud()
                second = tracker._cloud()  # cached
        self.assertEqual(first, "cloud-instance")
        self.assertIs(first, second)
        cloud_cls.assert_called_once()
        _, kwargs = cloud_cls.call_args
        self.assertEqual(kwargs["url"], "https://api.bitbucket.org/")
        self.assertEqual(kwargs["username"], "u")
        self.assertEqual(kwargs["password"], "p")
        self.assertEqual(kwargs["timeout"], 30)


if __name__ == "__main__":
    unittest.main()
