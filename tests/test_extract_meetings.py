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

# ─── MeetingProvider registry + factory ─────────────────────────────────────


class MeetingRegistryTests(unittest.TestCase):
    def test_fireflies_kind_registered(self) -> None:
        from briar.extract._meetings import MeetingProviderRegistry

        self.assertIn("fireflies", MeetingProviderRegistry.kinds())

    def test_unknown_kind_raises(self) -> None:
        from briar.errors import CliError
        from briar.extract._meetings import make_meeting

        with self.assertRaises(CliError):
            make_meeting("otter", company="acme")

    def test_fireflies_unavailable_without_creds(self) -> None:
        from briar.extract._meetings import make_meeting

        with mock.patch.dict("os.environ", {}, clear=True):
            provider = make_meeting("fireflies", company="acme")
            self.assertFalse(provider.is_available())

    def test_fireflies_required_env_vars(self) -> None:
        from briar.extract._meetings.fireflies import FirefliesMeetingProvider

        required = FirefliesMeetingProvider.required_env_vars(company="acme")
        self.assertEqual(required, ["FIREFLIES_ACME_API_KEY"])
        # Empty company = no required var (matches every other provider)
        self.assertEqual(FirefliesMeetingProvider.required_env_vars(company=""), [])


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
        with mock.patch.dict("os.environ", {"FIREFLIES_ACME_API_KEY": "ff_xxx"}):
            provider = make_meeting("fireflies", company="acme")
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

        with mock.patch.dict("os.environ", {"FIREFLIES_ACME_API_KEY": "ff_xxx"}):
            provider = make_meeting("fireflies", company="acme")
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
        with mock.patch.dict("os.environ", {"FIREFLIES_ACME_API_KEY": "ff_xxx"}):
            provider = make_meeting("fireflies", company="acme")
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
        from briar.extract.base import EMPTY_SECTION
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
        self.assertIs(section, EMPTY_SECTION)

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


class MeetingContextExtractorTests(unittest.TestCase):
    def test_empty_when_no_key_and_no_query(self) -> None:
        from briar.extract.base import EMPTY_SECTION
        from briar.extract.meeting_context import FetchMeetingContext

        ext = FetchMeetingContext()
        ns = argparse.Namespace(
            company="acme",
            meeting="fireflies",
            meeting_key="",
            meeting_query="",
            meeting_top_k=3,
            meeting_max_bytes=50_000,
        )
        with mock.patch.object(ext, "_meeting") as make_provider:
            provider = mock.MagicMock()
            provider.is_available.return_value = True
            make_provider.return_value = provider
            section = ext.fetch(ns)
        self.assertIs(section, EMPTY_SECTION)

    def test_fetch_by_id_renders_transcript(self) -> None:
        from briar.extract._meeting import Meeting, MeetingDetail
        from briar.extract.meeting_context import FetchMeetingContext

        ext = FetchMeetingContext()
        ns = argparse.Namespace(
            company="acme",
            meeting="fireflies",
            meeting_key="FF-1",
            meeting_query="",
            meeting_top_k=3,
            meeting_max_bytes=50_000,
        )
        detail = MeetingDetail(
            meeting=Meeting(
                meeting_id="FF-1",
                title="OAuth design call",
                started_at="2026-05-20T15:00:00+00:00",
                duration_sec=2700,
                organizer="alice@acme.com",
                attendees=["alice@acme.com", "bob@acme.com"],
                summary="Decided to use refresh tokens.",
                action_items=["Alice: write spec"],
            ),
            transcript="**Alice**: We use refresh tokens.\n\n**Bob**: Agreed.",
            topics=["OAuth", "Auth"],
        )
        with mock.patch.object(ext, "_meeting") as make_provider:
            provider = mock.MagicMock()
            provider.is_available.return_value = True
            provider.get_meeting.return_value = detail
            make_provider.return_value = provider
            section = ext.fetch(ns)
        self.assertFalse(section.is_empty)
        self.assertIn("OAuth design call", section.title)
        self.assertIn("refresh tokens", section.body)
        self.assertEqual(section.data["mode"], "by-id")

    def test_transcript_truncated_at_max_bytes(self) -> None:
        from briar.extract._meeting import Meeting, MeetingDetail
        from briar.extract.meeting_context import FetchMeetingContext

        ext = FetchMeetingContext()
        ns = argparse.Namespace(
            company="acme",
            meeting="fireflies",
            meeting_key="FF-1",
            meeting_query="",
            meeting_top_k=3,
            meeting_max_bytes=1024,
        )
        # 20 KB of transcript, capped at 1 KB
        long_text = "**Alice**: " + ("blah " * 4000)
        detail = MeetingDetail(
            meeting=Meeting(
                meeting_id="FF-1",
                title="Long meeting",
                started_at="2026-05-20T15:00:00+00:00",
                duration_sec=3600,
                organizer="",
            ),
            transcript=long_text,
        )
        with mock.patch.object(ext, "_meeting") as make_provider:
            provider = mock.MagicMock()
            provider.is_available.return_value = True
            provider.get_meeting.return_value = detail
            make_provider.return_value = provider
            section = ext.fetch(ns)
        self.assertIn("transcript truncated", section.body)
        # Truncated body should be smaller than the full content
        self.assertLess(len(section.body.encode("utf-8")), 5_000)

    def test_fetch_by_query_returns_multiple_matches(self) -> None:
        from briar.extract._meeting import Meeting, MeetingDetail
        from briar.extract.meeting_context import FetchMeetingContext

        ext = FetchMeetingContext()
        ns = argparse.Namespace(
            company="acme",
            meeting="fireflies",
            meeting_key="",
            meeting_query="ACME-123",
            meeting_top_k=2,
            meeting_max_bytes=10_000,
        )
        matches = [
            Meeting(meeting_id="FF-1", title="Standup mentioning ACME-123", started_at="2026-05-20T10:00:00+00:00", duration_sec=900, organizer=""),
            Meeting(meeting_id="FF-2", title="Planning re ACME-123", started_at="2026-05-21T10:00:00+00:00", duration_sec=1500, organizer=""),
        ]
        details = {
            "FF-1": MeetingDetail(meeting=matches[0], transcript="**Alice**: ACME-123 should ship."),
            "FF-2": MeetingDetail(meeting=matches[1], transcript="**Bob**: Confirmed for ACME-123."),
        }
        with mock.patch.object(ext, "_meeting") as make_provider:
            provider = mock.MagicMock()
            provider.is_available.return_value = True
            provider.search_meetings.return_value = matches
            provider.get_meeting.side_effect = lambda mid: details[mid]
            make_provider.return_value = provider
            section = ext.fetch(ns)
        self.assertFalse(section.is_empty)
        self.assertIn("2 match", section.title)
        self.assertIn("ACME-123 should ship", section.body)
        self.assertIn("Confirmed for ACME-123", section.body)
        self.assertEqual(section.data["mode"], "search")


# ─── Agent CLI wiring (--meeting-* flags + helper) ──────────────────────────


class AgentMeetingWiringTests(unittest.TestCase):
    def test_implement_op_registers_meeting_flags(self) -> None:
        from briar.commands.agent import ImplementOp

        parser = argparse.ArgumentParser()
        ImplementOp().add_arguments(parser)
        flags = {a.dest for a in parser._actions}
        for expected in ("meeting", "meeting_key", "meeting_query", "meeting_top_k", "meeting_max_bytes"):
            self.assertIn(expected, flags)

    def test_prfix_op_registers_meeting_flags(self) -> None:
        from briar.commands.agent import PrfixOp

        parser = argparse.ArgumentParser()
        PrfixOp().add_arguments(parser)
        flags = {a.dest for a in parser._actions}
        for expected in ("meeting", "meeting_key", "meeting_query", "meeting_top_k", "meeting_max_bytes"):
            self.assertIn(expected, flags)

    def test_fetch_meeting_context_empty_when_no_inputs(self) -> None:
        from briar.commands.agent import CommandAgent

        result = CommandAgent._fetch_meeting_context(
            company="acme",
            meeting_kind="fireflies",
            meeting_key="",
            meeting_query="",
            meeting_top_k=3,
            meeting_max_bytes=50_000,
        )
        self.assertEqual(result, [])

    def test_fetch_meeting_context_returns_section_on_match(self) -> None:
        from briar.commands.agent import CommandAgent
        from briar.extract.base import ExtractedSection

        fake_section = ExtractedSection(title="Meeting context — match", body="...")
        with mock.patch.dict("briar.extract.TASK_SCOPED_EXTRACTORS", {}, clear=False):
            mock_extractor = mock.MagicMock()
            mock_extractor.fetch.return_value = fake_section
            with mock.patch.dict("briar.extract.TASK_SCOPED_EXTRACTORS", {"meeting-context": mock_extractor}):
                result = CommandAgent._fetch_meeting_context(
                    company="acme",
                    meeting_kind="fireflies",
                    meeting_key="FF-1",
                    meeting_query="",
                    meeting_top_k=3,
                    meeting_max_bytes=50_000,
                )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "Meeting context — match")


# ─── KnowledgeSplicer + archetype consumes wiring ──────────────────────────


class MeetingArchetypeWiringTests(unittest.TestCase):
    def test_engineer_consumes_meeting_sections(self) -> None:
        from briar.iac.scaffold.archetypes.engineer import ArchetypeEngineer

        self.assertIn("meeting-context", ArchetypeEngineer.consumes)
        self.assertIn("meeting-digest", ArchetypeEngineer.consumes)

    def test_pr_fixer_consumes_meeting_sections(self) -> None:
        from briar.iac.scaffold.archetypes.pr_fixer import ArchetypePrFixer

        self.assertIn("meeting-context", ArchetypePrFixer.consumes)
        self.assertIn("meeting-digest", ArchetypePrFixer.consumes)

    def test_knowledge_splicer_recognises_meeting_headings(self) -> None:
        from briar.iac.scaffold._knowledge import _EXTRACTOR_HEADINGS

        self.assertEqual(_EXTRACTOR_HEADINGS["meeting-digest"], "Meeting digest")
        self.assertEqual(_EXTRACTOR_HEADINGS["meeting-context"], "Meeting context")


if __name__ == "__main__":
    unittest.main()
