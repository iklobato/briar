"""Behaviour tests for the ticket-context (task-scoped) fetch layer.

`FetchTicketContext.fetch` pulls ONE ticket's full body + ACs +
comments + status transitions and renders a single agent-prompt
section. The tracker is mocked at the `get_ticket` / `list_comments` /
`list_status_transitions` seams. It lives in TASK_SCOPED_EXTRACTORS,
not EXTRACTORS.

`get_ticket` returns None on any adapter failure (the verb is wrapped
in `swallow_errors(default=None)`), and the fetch layer converts
None → empty sentinel. `Ticket` / `Comment` fixtures model the
vendor-neutral shapes a provider normalises (e.g. Jira issue +
comments, see
https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-get
).
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract import TASK_SCOPED_EXTRACTORS
from briar.extract._tracker import Comment, Ticket, TrackerProvider


def _ticket(**over):
    base = dict(
        key="PROJ-123",
        title="Fix the widget",
        reporter="alice",
        assignee="bob",
        status="in_progress",
        kind="bug",
        priority="P1",
        created_at="2026-06-01T00:00:00Z",
        labels=["backend"],
        description="The widget breaks when X.",
    )
    base.update(over)
    return Ticket(**base)


class _ContextTracker(TrackerProvider):
    kind = "fake"

    def __init__(self, *, ticket=None, comments=None, transitions=None, company=""):
        self._company = company
        self._ticket = ticket
        self._comments = comments or []
        self._transitions = transitions or []

    def is_available(self):
        return True

    def list_tickets(self, project, *, state, max_count):
        return []

    def get_ticket(self, project, ticket_key):
        return self._ticket

    def list_comments(self, project, ticket_key):
        return list(self._comments)

    def list_status_transitions(self, project, ticket_key):
        return list(self._transitions)


def _args(project="PROJ", key="PROJ-123"):
    return argparse.Namespace(ticket_project=project, ticket_key=key, tracker="fake", company="")


def _run(tracker, args):
    ext = TASK_SCOPED_EXTRACTORS["ticket-context"]
    orig = ext._tracker
    ext._tracker = lambda a: tracker  # type: ignore[assignment]
    try:
        return ext.fetch(args)
    finally:
        ext._tracker = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_fetch_renders_full_ticket_with_metadata_and_comments():
    tracker = _ContextTracker(
        ticket=_ticket(),
        comments=[
            Comment(author="carol", body="repro confirmed", created_at="2026-06-02T00:00:00Z"),
            Comment(author="bob", body="fix on the way", created_at="2026-06-03T00:00:00Z"),
        ],
        transitions=["open", "in_progress"],
    )
    section = _run(tracker, _args())

    assert section.title == "Ticket context — PROJ-123: Fix the widget"
    assert section.data == {
        "key": "PROJ-123",
        "title": "Fix the widget",
        "status": "in_progress",
        "labels": ["backend"],
        "comment_count": 2,
    }

    body = section.body
    assert "**Key**: PROJ-123" in body
    assert "**Status**: in_progress" in body
    assert "**Type**: bug" in body
    assert "**Priority**: P1" in body
    assert "**Reporter**: alice" in body
    assert "**Assignee**: bob" in body
    assert "**Labels**: backend" in body
    assert "**Status history**: open → in_progress" in body
    assert "The widget breaks when X." in body
    assert "### Comments (2)" in body
    assert "**carol** (2026-06-02T00:00:00Z):" in body
    assert "repro confirmed" in body


@pytest.mark.unit
def test_unset_reporter_and_assignee_render_placeholder():
    tracker = _ContextTracker(ticket=_ticket(reporter="", assignee=""))
    body = _run(tracker, _args()).body
    assert "**Reporter**: (unset)" in body
    assert "**Assignee**: (unset)" in body


@pytest.mark.unit
def test_missing_description_renders_placeholder():
    tracker = _ContextTracker(ticket=_ticket(description=""))
    body = _run(tracker, _args()).body
    assert "_(no description)_" in body


@pytest.mark.unit
def test_comments_truncated_at_twenty_with_overflow_note():
    comments = [Comment(author=f"u{i}", body=f"c{i}", created_at="2026-06-01T00:00:00Z") for i in range(25)]
    section = _run(_ContextTracker(ticket=_ticket(), comments=comments), _args())
    body = section.body
    # data carries the true total...
    assert section.data["comment_count"] == 25
    # ...but only the first 20 bodies are inlined.
    assert "c19" in body
    assert "c20" not in body.split("…and")[0]
    assert "_…and 5 more (older); fetch with --max-comments to see all_" in body


@pytest.mark.unit
def test_none_ticket_yields_empty_section(caplog_briar):
    # get_ticket returned None (adapter swallowed an auth/network error).
    section = _run(_ContextTracker(ticket=None), _args(key="PROJ-999"))
    assert section.is_empty
    assert "PROJ-999 not found or empty" in caplog_briar.text


@pytest.mark.unit
def test_empty_titleless_descriptionless_ticket_yields_empty_section():
    # A ticket that exists but has neither title nor description is
    # treated as "not found" — the boundary that converts the empty
    # default Ticket into the empty sentinel.
    blank = _ticket(title="", description="")
    section = _run(_ContextTracker(ticket=blank), _args())
    assert section.is_empty


@pytest.mark.unit
def test_ticket_with_only_title_renders_section():
    # A title with no description is still a real ticket (the guard is
    # AND, not OR) — must NOT collapse to empty.
    tracker = _ContextTracker(ticket=_ticket(title="Has title", description=""))
    section = _run(tracker, _args())
    assert not section.is_empty
    assert "Has title" in section.title


@pytest.mark.unit
def test_no_comments_omits_comments_block():
    section = _run(_ContextTracker(ticket=_ticket(), comments=[]), _args())
    assert "### Comments" not in section.body
    assert section.data["comment_count"] == 0
