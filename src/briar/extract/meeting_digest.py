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
import logging
from datetime import datetime, timedelta, timezone
from typing import ClassVar, List

from briar.auth.logins import CompanyLogins
from briar.errors import CliError
from briar.extract._meeting import Meeting, MeetingPage, MeetingProvider, render_meeting_header
from briar.extract._meetings.company_filter import CompanyMembers, MeetingFilter, MemberSources, MembersUnreadable
from briar.extract.base import ExtractedSection, MeetingBackedExtractor, empty_section


_DEFAULT_SINCE_DAYS = 7
_DEFAULT_MAX = 25
_SUMMARY_TRUNC = 600
_ACTION_ITEMS_TRUNC = 8


class ExtractMeetingDigest(MeetingBackedExtractor):
    name = "meeting-digest"
    heading = "Meeting digest"
    description = "recent meetings: summaries + action items"
    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)

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
        self._add_company_filter(parser)
        self._add_member_sources(parser)

    def _add_company_filter(self, parser: argparse.ArgumentParser) -> None:
        """F1: one key can hold the meetings of several companies."""
        parser.add_argument(
            "--meeting-attendee-allow",
            action="append",
            default=[],
            help="Keep meetings with one of these attendee or organizer emails; `@acme.com` keeps a whole domain. Repeatable.",
        )
        parser.add_argument(
            "--meeting-title-any",
            action="append",
            default=[],
            help="Keep meetings whose title contains one of these words (any case). Repeatable. "
            "With no title word, no email and no Jira, GitLab or Slack login, every meeting is kept.",
        )

    def _add_member_sources(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--meeting-jira-project",
            action="append",
            default=[],
            help="Jira projects whose people count as the company for the speakers (default: every user of the site, "
            "which a plain account often cannot read). Repeatable.",
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

        now = datetime.now(tz=timezone.utc)
        since = (now - timedelta(days=since_days)).isoformat()
        until = now.isoformat()

        try:
            rules = self._rules(args)
        except MembersUnreadable as stop:
            self.LOG.error("meeting-digest stopped: %s", stop)
            return empty_section()
        meetings = MeetingPages(provider, rules).kept(MeetingPage(since, until, max_count))
        if not meetings:
            return empty_section()

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
            data={
                "meeting_count": len(meetings),
                "since_iso": since,
                "until_iso": until,
            },
        )

    def _rules(self, args: argparse.Namespace) -> MeetingFilter:
        """F1, F12: the title words, the allowed emails and the members of the company."""
        projects = tuple(getattr(args, "meeting_jira_project", []) or [])
        members = CompanyMembers(MemberSources(CompanyLogins.from_args(args), projects).configured()).keys()
        titles = tuple(getattr(args, "meeting_title_any", []) or [])
        return MeetingFilter(titles, tuple(getattr(args, "meeting_attendee_allow", []) or []), members)

    @staticmethod
    def _render_meeting(m: Meeting) -> ExtractedSection:
        lines: List[str] = render_meeting_header(m, attendee_cap=8, show_more_suffix=True)
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


class MeetingPages:
    """F8: the pages of the window until `page.size` meetings pass the filter or a page comes
    back short (the window ended). A page is `page.size` long, so with no filter one call
    is enough, as before."""

    def __init__(self, provider: MeetingProvider, rules: MeetingFilter) -> None:
        self._provider = provider
        self._rules = rules

    def kept(self, page: MeetingPage) -> List[Meeting]:
        wanted = page.size
        kept: List[Meeting] = []
        while len(kept) < wanted:
            found = self._provider.list_meetings(page)
            kept += [meeting for meeting in found if self._rules.keeps(meeting)]
            if len(found) < page.size:
                break
            page = page.after(len(found))
        return kept[:wanted]
