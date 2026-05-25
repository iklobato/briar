"""Scheduled: recent meetings digest — summaries + action items.

Pulls the last N days of meetings from the configured `MeetingProvider`
and emits one section per meeting (header + summary + action items +
attendee list + URL). The agent reads this on every run so decisions
made in standups land in implementation + prfix without an explicit
``--meeting-key`` flag.

Meeting-provider-agnostic: talks to a `MeetingProvider`, never to
Fireflies directly. ``--meeting otter`` (future) routes the same
logic onto a different vendor."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from typing import List

from briar.errors import CliError
from briar.extract._meeting import Meeting
from briar.extract._enums import MeetingExtractMode
from briar.extract._types import MeetingExtractedData
from briar.extract.base import EMPTY_SECTION, ExtractedSection, MeetingBackedExtractor

_DEFAULT_SINCE_DAYS = 7
_DEFAULT_MAX = 25
_SUMMARY_TRUNC = 600
_ACTION_ITEMS_TRUNC = 8


class ExtractMeetingDigest(MeetingBackedExtractor):
    name = "meeting-digest"
    description = "recent meetings: summaries + action items"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--meeting-since-days",
            type=int,
            default=_DEFAULT_SINCE_DAYS,
            help=f"How many days back to scan (default: {_DEFAULT_SINCE_DAYS})",
        )
        parser.add_argument(
            "--meeting-max",
            type=int,
            default=_DEFAULT_MAX,
            help=f"Cap on meetings included in the digest (default: {_DEFAULT_MAX})",
        )
        parser.add_argument(
            "--meeting-attendee-allow",
            action="append",
            default=[],
            help="Only include meetings with at least one of these attendee emails. " "Repeatable. Empty = no filter (every accessible meeting).",
        )

    def is_available(self, args: argparse.Namespace) -> bool:
        try:
            provider = self._meeting(args)
        except CliError:
            # Expected when creds are missing — degrade silently.
            # Other exceptions (typos on args.company, etc.) propagate
            # loud — was `except Exception` which hid real bugs.
            return False
        return provider.is_available()

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._meeting(args)
        since_days = max(int(getattr(args, "meeting_since_days", _DEFAULT_SINCE_DAYS)), 1)
        max_count = max(int(getattr(args, "meeting_max", _DEFAULT_MAX)), 1)
        attendees = list(getattr(args, "meeting_attendee_allow", []) or [])

        now = datetime.now(tz=timezone.utc)
        since = (now - timedelta(days=since_days)).isoformat()
        until = now.isoformat()

        meetings: List[Meeting] = provider.list_meetings(
            since_iso=since,
            until_iso=until,
            max_count=max_count,
            attendees=attendees,
        )
        if not meetings:
            return EMPTY_SECTION

        subsections = [self._render_meeting(m) for m in meetings]
        return ExtractedSection(
            title=f"Meeting digest — {len(meetings)} meeting(s), last {since_days} day(s)",
            body=(
                "Recent meetings: each subsection has the date, attendees, AI-generated "
                "summary, and action items. Treat decisions captured here as binding for "
                "implementation. If a meeting is referenced by a ticket but its summary "
                "looks incomplete, fetch the full transcript via the `meeting-context` "
                "extractor (operator must pass `--meeting-key <id>`)."
            ),
            subsections=subsections,
            data=dict(MeetingExtractedData(
                mode=MeetingExtractMode.DIGEST,
                meeting_count=len(meetings),
                since_iso=since,
                until_iso=until,
            )),
        )

    @staticmethod
    def _render_meeting(m: Meeting) -> ExtractedSection:
        lines: List[str] = [
            f"**ID**: `{m.meeting_id}`",
            f"**Date**: {m.started_at or '(unknown)'}",
        ]
        if m.organizer:
            lines.append(f"**Organizer**: {m.organizer}")
        if m.attendees:
            preview = ", ".join(m.attendees[:8])
            if len(m.attendees) > 8:
                preview += f" (+{len(m.attendees) - 8} more)"
            lines.append(f"**Attendees**: {preview}")
        if m.duration_sec:
            lines.append(f"**Duration**: {m.duration_sec // 60} min")
        if m.url:
            lines.append(f"**URL**: {m.url}")
        lines.append("")
        if m.summary:
            lines.append("**Summary**:")
            lines.append(m.summary[:_SUMMARY_TRUNC])
            lines.append("")
        if m.action_items:
            lines.append(f"**Action items** ({len(m.action_items)}):")
            for item in m.action_items[:_ACTION_ITEMS_TRUNC]:
                lines.append(f"- {item}")
            if len(m.action_items) > _ACTION_ITEMS_TRUNC:
                lines.append(f"- _…and {len(m.action_items) - _ACTION_ITEMS_TRUNC} more_")
        title = f"{m.started_at[:10] if m.started_at else 'unknown'} — {m.title or '(untitled)'}"
        return ExtractedSection(
            title=title,
            body="\n".join(lines),
            data={
                "meeting_id": m.meeting_id,
                "title": m.title,
                "started_at": m.started_at,
                "duration_sec": m.duration_sec,
                "attendees": m.attendees,
                "action_item_count": len(m.action_items),
            },
        )
