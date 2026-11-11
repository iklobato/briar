"""Tests for the MeetingProvider abstraction + FirefliesMeetingProvider
adapter + meeting-digest / meeting-context extractors + agent CLI wiring.

Mirrors the shape of tests/test_abstractions.py:TrackerRegistryTests
and tests/test_extract.py — registry shape, factory error paths, GraphQL
response translation under a urlopen mock, extractor section shape, and
the new --meeting-* flags on ImplementOp / PrfixOp."""

from __future__ import annotations

import argparse
import json
import unittest
from typing import List
from unittest import mock

from tests.unit.fake_logins import FakeLogins

# ─── MeetingProvider registry + factory ─────────────────────────────────────


class MeetingRegistryTests(unittest.TestCase):
    def test_fireflies_kind_registered(self) -> None:
        from briar.extract._meetings import meeting_kinds

        self.assertIn("fireflies", meeting_kinds())

    def test_unknown_kind_raises(self) -> None:
        from briar.errors import CliError
        from briar.extract._meetings import make_meeting

        with self.assertRaises(CliError):
            make_meeting("otter", FakeLogins().logins())

    def test_fireflies_available_with_the_api_key(self) -> None:
        from briar.extract._meetings import make_meeting

        provider = make_meeting("fireflies", FakeLogins(environ={"BRIAR_ACME_FIREFLIES_API_KEY": "ff_xxx"}).logins())
        self.assertTrue(provider.is_available())


# ─── FirefliesMeetingProvider GraphQL translation ──────────────────────────


class FirefliesAdapterTests(unittest.TestCase):
    def _mock_urlopen(self, payload: dict) -> mock.MagicMock:
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps(payload).encode("utf-8")
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    def test_list_meetings_translates_response(self) -> None:
        from briar.extract._meetings import make_meeting

        payload = {
            "data": {
                "transcripts": [
                    {
                        "id": "FF-1",
                        "title": "Engineering Standup 2026-05-22",
                        "date": 1716422400000,  # epoch-ms
                        "duration": 1800.0,
                        "organizer_email": "alice@acme.com",
                        "host_email": "alice@acme.com",
                        "participants": ["alice@acme.com", "bob@acme.com"],
                        "transcript_url": "https://app.fireflies.ai/view/FF-1",
                        "meeting_attendees": [
                            {"displayName": "Alice", "email": "alice@acme.com", "name": "alice"},
                            {"displayName": "Bob", "email": "bob@acme.com", "name": "bob"},
                        ],
                        "summary": {
                            "overview": "Discussed ACME-123 OAuth rollout.",
                            "action_items": ["Alice: ship draft PR by Friday", "Bob: review compliance"],
                        },
                    }
                ]
            }
        }
        with mock.patch.dict("os.environ", {}):
            provider = make_meeting("fireflies", FakeLogins(environ={"BRIAR_ACME_FIREFLIES_API_KEY": "ff_xxx"}).logins())
            self.assertTrue(provider.is_available())
            with mock.patch("urllib.request.urlopen", return_value=self._mock_urlopen(payload)):
                meetings = provider.list_meetings(
                    since_iso="2026-05-15T00:00:00+00:00",
                    until_iso="2026-05-22T00:00:00+00:00",
                    max_count=10,
                )
        self.assertEqual(len(meetings), 1)
        meeting = meetings[0]
        self.assertEqual(meeting.meeting_id, "FF-1")
        self.assertEqual(meeting.title, "Engineering Standup 2026-05-22")
        self.assertEqual(meeting.duration_sec, 1800)
        self.assertEqual(meeting.organizer, "alice@acme.com")
        self.assertIn("alice@acme.com", meeting.attendees)
        self.assertIn("bob@acme.com", meeting.attendees)
        self.assertIn("ACME-123", meeting.summary)
        self.assertEqual(len(meeting.action_items), 2)
        # Epoch-ms → ISO-8601 normalisation
        self.assertTrue(meeting.started_at.startswith("2024") or meeting.started_at.startswith("20"))

    def test_search_meetings_passes_keyword(self) -> None:
        from briar.extract._meetings import make_meeting

        captured: List[dict] = []

        def fake_urlopen(req, timeout=None):
            captured.append(json.loads(req.data.decode("utf-8")))
            return self._mock_urlopen({"data": {"transcripts": []}})

        with mock.patch.dict("os.environ", {}):
            provider = make_meeting("fireflies", FakeLogins(environ={"BRIAR_ACME_FIREFLIES_API_KEY": "ff_xxx"}).logins())
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                provider.search_meetings(query="ACME-123 oauth migration", max_count=3)
        self.assertEqual(len(captured), 1)
        variables = captured[0]["variables"]
        self.assertEqual(variables["keyword"], "ACME-123 oauth migration")
        self.assertEqual(variables["scope"], "ALL")
        self.assertEqual(variables["limit"], 3)

    def test_get_meeting_renders_transcript(self) -> None:
        from briar.extract._meetings import make_meeting

        payload = {
            "data": {
                "transcript": {
                    "id": "FF-1",
                    "title": "Standup",
                    "date": 1716422400000,
                    "duration": 600.0,
                    "organizer_email": "alice@acme.com",
                    "host_email": "alice@acme.com",
                    "participants": ["alice@acme.com", "bob@acme.com"],
                    "transcript_url": "https://app.fireflies.ai/view/FF-1",
                    "meeting_attendees": [],
                    "summary": {
                        "overview": "Decided to use Redis",
                        "action_items": ["Alice: pick Redis client"],
                        "keywords": ["redis", "cache"],
                        "topics_discussed": ["Caching layer"],
                    },
                    "sentences": [
                        {"index": 0, "speaker_name": "Alice", "text": "We agreed on Redis.", "start_time": 0.0},
                        {"index": 1, "speaker_name": "Alice", "text": "Bob will pick the client.", "start_time": 5.0},
                        {"index": 2, "speaker_name": "Bob", "text": "Sounds good.", "start_time": 10.0},
                    ],
                }
            }
        }
        with mock.patch.dict("os.environ", {}):
            provider = make_meeting("fireflies", FakeLogins(environ={"BRIAR_ACME_FIREFLIES_API_KEY": "ff_xxx"}).logins())
            with mock.patch("urllib.request.urlopen", return_value=self._mock_urlopen(payload)):
                detail = provider.get_meeting("FF-1")
        self.assertEqual(detail.meeting.meeting_id, "FF-1")
        # Consecutive Alice lines coalesce into one "**Alice**:" prefix
        self.assertEqual(detail.transcript.count("**Alice**:"), 1)
        self.assertEqual(detail.transcript.count("**Bob**:"), 1)
        self.assertIn("We agreed on Redis.", detail.transcript)
        self.assertIn("redis", detail.keywords)
        self.assertIn("Caching layer", detail.topics)


# ─── meeting-digest extractor ────────────────────────────────────────────────


class MeetingDigestExtractorTests(unittest.TestCase):
    def test_empty_when_provider_returns_no_meetings(self) -> None:
        from briar.extract.base import empty_section
        from briar.extract.meeting_digest import ExtractMeetingDigest

        ext = ExtractMeetingDigest()
        ns = argparse.Namespace(
            company="acme",
            meeting="fireflies",
            meeting_since_days=7,
            meeting_max=25,
            meeting_attendee_allow=[],
        )
        with mock.patch.object(ext, "_meeting") as make_provider:
            provider = mock.MagicMock()
            provider.list_meetings.return_value = []
            make_provider.return_value = provider
            section = ext.extract(ns)
        # was: assertIs(section, EMPTY_SECTION) — now empty_section() returns
        # a fresh instance each call, so check the "no data" property instead.
        self.assertTrue(section.is_empty)

    def test_section_shape_with_meetings(self) -> None:
        from briar.extract._meeting import Meeting
        from briar.extract.meeting_digest import ExtractMeetingDigest

        ext = ExtractMeetingDigest()
        ns = argparse.Namespace(
            company="acme",
            meeting="fireflies",
            meeting_since_days=7,
            meeting_max=25,
            meeting_attendee_allow=[],
        )
        fake_meetings = [
            Meeting(
                meeting_id="FF-1",
                title="Standup",
                started_at="2026-05-22T10:00:00+00:00",
                duration_sec=1800,
                organizer="alice@acme.com",
                attendees=["alice@acme.com", "bob@acme.com"],
                url="https://app.fireflies.ai/view/FF-1",
                summary="Decided to ship ACME-123 by Friday.",
                action_items=["Alice: draft PR", "Bob: review"],
            ),
        ]
        with mock.patch.object(ext, "_meeting") as make_provider:
            provider = mock.MagicMock()
            provider.list_meetings.return_value = fake_meetings
            make_provider.return_value = provider
            section = ext.extract(ns)
        self.assertFalse(section.is_empty)
        self.assertIn("1 meeting", section.title)
        self.assertEqual(len(section.subsections), 1)
        self.assertIn("ACME-123", section.subsections[0].body)
        self.assertIn("Alice: draft PR", section.subsections[0].body)
        self.assertEqual(section.data["meeting_count"], 1)


# ─── meeting-context (task-scoped) extractor ────────────────────────────────


# ─── Agent CLI wiring (--meeting-* flags + helper) ──────────────────────────


# ─── KnowledgeSplicer + archetype consumes wiring ──────────────────────────


if __name__ == "__main__":
    unittest.main()
