"""Behaviour tests for the ticket-archaeology compose layer.

`ExtractTicketArchaeology` mines closed tickets per project and reports
median time-to-close, top reporters/assignees/labels, and ticket kinds.
The tracker is mocked at the `list_tickets` seam with a hand-rolled
`TrackerProvider` subclass (same pattern as the new tracker unit tests).

`Ticket` fixtures model the vendor-neutral shape providers normalise
from, e.g. Jira search results — `created` / `updated` /
`reporter.displayName` / `assignee.displayName` / `labels` /
`issuetype.name`, see
https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-get
"""

from __future__ import annotations

import argparse

import pytest

from briar.extract import EXTRACTORS
from briar.extract._tracker import Ticket, TrackerProvider


def _ticket(key, *, reporter="r", assignee="a", kind="bug", labels=(), created="2026-06-01T00:00:00Z", updated="2026-06-01T05:00:00Z"):
    return Ticket(
        key=key,
        title=f"t {key}",
        reporter=reporter,
        assignee=assignee,
        status="closed",
        kind=kind,
        priority="P2",
        created_at=created,
        updated_at=updated,
        labels=list(labels),
    )


class _TicketTracker(TrackerProvider):
    kind = "fake"

    def __init__(self, tickets=None, *, company="", raises=None):
        self._company = company
        self._tickets = tickets or []
        self._raises = raises

    def is_available(self):
        return True

    def list_tickets(self, project, *, state, max_count):
        self._state = state
        self._max = max_count
        if self._raises:
            raise self._raises
        return list(self._tickets)


def _args(projects=("PROJ",), ticket_max=100):
    return argparse.Namespace(
        ticket_archaeology_project=list(projects),
        ticket_max=ticket_max,
        tracker="fake",
        company="",
    )


def _run(tracker, args):
    ext = EXTRACTORS["ticket-archaeology"]
    orig = ext._tracker
    ext._tracker = lambda a: tracker  # type: ignore[assignment]
    try:
        return ext.extract(args)
    finally:
        ext._tracker = orig  # type: ignore[assignment]


@pytest.mark.unit
def test_compose_reports_median_and_top_aggregates():
    tickets = [
        # close times: 2h, 4h, 6h → median 4.0
        _ticket("P-1", reporter="alice", assignee="bob", kind="bug", labels=["backend"], created="2026-06-01T00:00:00Z", updated="2026-06-01T02:00:00Z"),
        _ticket(
            "P-2", reporter="alice", assignee="carol", kind="bug", labels=["backend", "urgent"], created="2026-06-01T00:00:00Z", updated="2026-06-01T04:00:00Z"
        ),
        _ticket("P-3", reporter="dave", assignee="bob", kind="feature", labels=["frontend"], created="2026-06-01T00:00:00Z", updated="2026-06-01T06:00:00Z"),
    ]
    section = _run(_TicketTracker(tickets), _args())

    assert section.title == "Ticket archaeology — 1 project(s)"
    proj = section.subsections[0]
    assert proj.title == "PROJ"
    data = proj.data
    assert data["closed_ticket_count"] == 3
    assert data["median_close_hours"] == 4.0
    # Counters ranked by frequency.
    assert data["top_reporters"][0] == ("alice", 2)
    assert ("bob", 2) in data["top_assignees"]
    assert data["top_labels"][0] == ("backend", 2)
    assert ("bug", 2) in data["kinds"]

    assert "closed ticket sample: **3**" in proj.body
    assert "median time-to-close: **4.0h**" in proj.body
    assert "top reporters: alice(2)" in proj.body
    assert "top labels: backend(2)" in proj.body


@pytest.mark.unit
def test_unparsable_timestamps_excluded_from_median():
    # One ticket has a garbage `updated_at` (parse → -1.0 sentinel,
    # filtered by `h >= 0`). Median computed from the valid one only.
    tickets = [
        _ticket("P-1", created="2026-06-01T00:00:00Z", updated="2026-06-01T10:00:00Z"),  # 10h
        _ticket("P-2", created="not-a-date", updated="also-bad"),  # -1.0 → dropped
    ]
    proj = _run(_TicketTracker(tickets), _args()).subsections[0]
    assert proj.data["closed_ticket_count"] == 2
    assert proj.data["median_close_hours"] == 10.0


@pytest.mark.unit
def test_all_unparsable_yields_none_median_but_section_present():
    tickets = [_ticket("P-1", created="bad", updated="bad")]
    proj = _run(_TicketTracker(tickets), _args()).subsections[0]
    assert proj.data["median_close_hours"] is None
    # median line is omitted, but the section is still rendered.
    assert "median time-to-close" not in proj.body
    assert "closed ticket sample: **1**" in proj.body


@pytest.mark.unit
def test_missing_reporter_assignee_falls_back_to_question_mark():
    tickets = [_ticket("P-1", reporter="", assignee="", kind="")]
    proj = _run(_TicketTracker(tickets), _args()).subsections[0]
    assert ("?", 1) in proj.data["top_reporters"]
    assert ("?", 1) in proj.data["top_assignees"]
    assert ("?", 1) in proj.data["kinds"]


@pytest.mark.unit
def test_empty_upstream_yields_empty_section():
    section = _run(_TicketTracker([]), _args())
    assert section.is_empty


@pytest.mark.unit
def test_list_tickets_called_with_closed_state_and_max():
    tracker = _TicketTracker([_ticket("P-1")])
    _run(tracker, _args(ticket_max=42))
    assert tracker._state == "closed"
    assert tracker._max == 42


@pytest.mark.unit
def test_multiple_projects_each_subsection():
    section = _run(_TicketTracker([_ticket("P-1")]), _args(projects=("A", "B")))
    assert section.title == "Ticket archaeology — 2 project(s)"
    assert [s.title for s in section.subsections] == ["A", "B"]


@pytest.mark.unit
def test_tracker_error_propagates():
    with pytest.raises(RuntimeError, match="401"):
        _run(_TicketTracker(raises=RuntimeError("401 unauthorized")), _args())
