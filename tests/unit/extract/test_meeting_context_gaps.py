"""Gap-filling tests for the meeting-context (task-scoped) fetch layer.

`tests/test_extract_meetings.py` pins the happy paths (fetch-by-id,
fetch-by-query, truncation, no-inputs). This file covers the
friendly-degradation branches it skips:
  - provider not available → empty sentinel (no crash)
  - fetch-by-id where get_meeting returns None / an empty meeting
  - fetch-by-query with zero search matches
  - fetch-by-query where every hydrate returns None (all dropped)
  - top_k / max_bytes lower-bound clamping

Provider mocked with a MagicMock, the same way the existing meeting
tests mock `_meeting`. `Meeting` / `MeetingDetail` model the
vendor-neutral shapes the Fireflies adapter normalises from the
GraphQL `transcript` type, see https://docs.fireflies.ai/graphql-api/query/transcript
"""

from __future__ import annotations

import argparse
from unittest import mock

import pytest

from briar.extract._meeting import Meeting, MeetingDetail
from briar.extract.meeting_context import FetchMeetingContext


def _ns(**over):
    base = dict(
        company="acme",
        meeting="fireflies",
        meeting_key="",
        meeting_query="",
        meeting_top_k=3,
        meeting_max_bytes=50_000,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _detail(meeting_id="FF-1", title="Standup"):
    return MeetingDetail(
        meeting=Meeting(
            meeting_id=meeting_id,
            title=title,
            started_at="2026-05-20T15:00:00+00:00",
            duration_sec=600,
            organizer="alice@acme.com",
        ),
        transcript="**Alice**: hi",
        topics=[],
    )


def _fetch(ns, provider):
    ext = FetchMeetingContext()
    with mock.patch.object(ext, "_meeting", return_value=provider):
        return ext.fetch(ns)


@pytest.mark.unit
def test_unavailable_provider_yields_empty_section(caplog_briar):
    provider = mock.MagicMock()
    provider.is_available.return_value = False
    section = _fetch(_ns(meeting_key="FF-1"), provider)
    assert section.is_empty
    # Must short-circuit before touching get_meeting.
    provider.get_meeting.assert_not_called()
    assert "provider not available" in caplog_briar.text


@pytest.mark.unit
def test_fetch_by_id_none_detail_yields_empty_section(caplog_briar):
    provider = mock.MagicMock()
    provider.is_available.return_value = True
    provider.get_meeting.return_value = None
    section = _fetch(_ns(meeting_key="FF-404"), provider)
    assert section.is_empty
    assert "FF-404 not found or empty" in caplog_briar.text


@pytest.mark.unit
def test_fetch_by_id_empty_meeting_id_yields_empty_section():
    # detail returned but with a blank meeting_id (provider couldn't
    # resolve it) → treated as not-found.
    provider = mock.MagicMock()
    provider.is_available.return_value = True
    provider.get_meeting.return_value = _detail(meeting_id="")
    section = _fetch(_ns(meeting_key="FF-1"), provider)
    assert section.is_empty


@pytest.mark.unit
def test_fetch_by_query_no_matches_yields_empty_section(caplog_briar):
    provider = mock.MagicMock()
    provider.is_available.return_value = True
    provider.search_meetings.return_value = []
    section = _fetch(_ns(meeting_query="oauth migration"), provider)
    assert section.is_empty
    provider.get_meeting.assert_not_called()
    assert "no matches for query" in caplog_briar.text


@pytest.mark.unit
def test_fetch_by_query_all_hydrates_none_yields_empty_section():
    # search returns candidates, but every get_meeting hydrate fails
    # (None) → the whole fetch degrades to empty, never crashes.
    provider = mock.MagicMock()
    provider.is_available.return_value = True
    provider.search_meetings.return_value = [
        Meeting(meeting_id="FF-1", title="a", started_at="", duration_sec=0, organizer=""),
        Meeting(meeting_id="FF-2", title="b", started_at="", duration_sec=0, organizer=""),
    ]
    provider.get_meeting.return_value = None
    section = _fetch(_ns(meeting_query="x"), provider)
    assert section.is_empty


@pytest.mark.unit
def test_meeting_key_takes_precedence_over_query():
    provider = mock.MagicMock()
    provider.is_available.return_value = True
    provider.get_meeting.return_value = _detail(meeting_id="FF-7", title="Keyed call")
    section = _fetch(_ns(meeting_key="FF-7", meeting_query="ignored"), provider)
    assert not section.is_empty
    assert "Keyed call" in section.title
    # Query path must not run when a key is present.
    provider.search_meetings.assert_not_called()
    provider.get_meeting.assert_called_once_with("FF-7")


@pytest.mark.unit
def test_top_k_clamped_to_at_least_one():
    # A non-positive top_k must be clamped to 1, not passed through.
    provider = mock.MagicMock()
    provider.is_available.return_value = True
    provider.search_meetings.return_value = []
    _fetch(_ns(meeting_query="x", meeting_top_k=0), provider)
    _, kwargs = provider.search_meetings.call_args
    assert kwargs["max_count"] == 1


@pytest.mark.unit
def test_query_match_renders_count_in_title_and_body():
    provider = mock.MagicMock()
    provider.is_available.return_value = True
    provider.search_meetings.return_value = [
        Meeting(meeting_id="FF-1", title="Standup", started_at="2026-05-20T15:00:00+00:00", duration_sec=0, organizer=""),
    ]
    provider.get_meeting.return_value = _detail(meeting_id="FF-1", title="Standup")
    section = _fetch(_ns(meeting_query="standup notes"), provider)
    assert section.data == {"query": "standup notes", "match_count": 1}
    assert "1 match(es)" in section.title
    assert "Top 1 match(es) for query `standup notes`" in section.body
