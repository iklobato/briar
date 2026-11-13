"""`MeetingProvider` — vendor-neutral facade for meeting-transcription tools.

Third source family alongside `RepositoryProvider` (code hosts) and
`TrackerProvider` (issue trackers). Meetings are transcript-centric,
identifier-less, time-windowed — different verbs from PRs or tickets,
so a separate ABC keeps each contract honest (Liskov).

Concrete adapters live in `_meetings/`. Today: Fireflies. Adding
Otter / Granola / Read.ai = one module + one registry entry; zero
extractor edits.

Extractors that consume meetings:
  ``meeting-digest``   scheduled — last N days of summaries + action items
  ``meeting-context``  JIT — full transcript of one meeting OR top-K
                       keyword matches against a ticket/PR title."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import ClassVar, List

from briar.auth.check import LoginRejected

log = logging.getLogger(__name__)


# Default knobs for meeting-context fetches — duplicated as argparse
# defaults across 6+ sites in commands/agent.py + commands/plan.py
# before centralisation. Keep them here so a single edit moves both
# the CLI defaults and the in-code fallbacks together.
DEFAULT_MEETING_TOP_K = 3
DEFAULT_MEETING_MAX_BYTES = 50_000


def render_meeting_header(
    m: "Meeting",
    *,
    attendee_cap: int = 12,
    show_more_suffix: bool = False,
) -> List[str]:
    """Return the canonical ID/Date/Organizer/Attendees/Duration/URL
    header lines for one meeting. `meeting_digest` passes
    `attendee_cap=8` with the `+N more` suffix; the defaults were the
    1.x `meeting_context` extractor's, removed in spec 006."""
    lines: List[str] = [
        f"**ID**: `{m.meeting_id}`",
        f"**Date**: {m.started_at or '(unknown)'}",
    ]
    if m.organizer:
        lines.append(f"**Organizer**: {m.organizer}")
    if m.attendees:
        preview = ", ".join(m.attendees[:attendee_cap])
        if show_more_suffix and len(m.attendees) > attendee_cap:
            preview += f" (+{len(m.attendees) - attendee_cap} more)"
        lines.append(f"**Attendees**: {preview}")
    if m.duration_sec:
        lines.append(f"**Duration**: {m.duration_sec // 60} min")
    if m.url:
        lines.append(f"**URL**: {m.url}")
    return lines


@dataclass(frozen=True)
class Meeting:
    """Vendor-neutral meeting header. The shape `list_meetings` and
    `search_meetings` return — body-less, so list calls stay cheap.

    Maps Fireflies' `id` / `title` / `date` (epoch-ms) /
    `organizer_email` / `participants` / `duration` / `transcript_url`
    / `summary.overview` / `summary.action_items` — onto these fields."""

    meeting_id: str
    title: str
    started_at: str  # ISO-8601 (provider's vocabulary, normalised by adapter)
    duration_sec: int
    organizer: str
    attendees: List[str] = field(default_factory=list)
    url: str = ""
    summary: str = ""
    action_items: List[str] = field(default_factory=list)
    # Names as the meeting tool heard them; the company filter matches them to members.
    speakers: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MeetingPage:
    """One page of a date window: at most `size` meetings from the `skip`-th one, most recent
    first. `since_iso` / `until_iso` are ISO-8601 timestamps."""

    since_iso: str
    until_iso: str
    size: int
    skip: int = 0

    def after(self, seen: int) -> "MeetingPage":
        """The page that starts after the `seen` meetings of this one."""
        return replace(self, skip=self.skip + seen)


@dataclass(frozen=True)
class MeetingDetail:
    """One meeting WITH its full transcript. Returned by `get_meeting`.

    The transcript is rendered as a markdown-ish string already — the
    adapter joins `sentences[].speaker_name + text` into one block so
    consumers don't have to reshape per-vendor sentence arrays. Keep
    the raw structured data in `data` for callers that need it."""

    meeting: Meeting
    transcript: str = ""
    topics: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)


class MeetingProvider(ABC):
    """Strategy contract. Concrete subclasses adapt one vendor onto
    these four verbs.

    Two list-style verbs (`list_meetings`, `search_meetings`) — time-
    window vs keyword-match — and one single-fetch verb (`get_meeting`)
    that hydrates the transcript. `is_available` gates the registry
    factory: a provider whose credentials are missing reports False
    and the extractor skips silently (matches `TrackerProvider`)."""

    kind: ClassVar[str] = ""
    # The login source the check before a run asks (spec 005 PR 3, J4); each adapter sets it.
    login_source: ClassVar[str] = ""

    def who_am_i(self) -> str:
        """The account of the login, asked of the source itself (the login check, J1)."""
        raise LoginRejected(f"{self.kind} has no login check")

    def read(self, resource: str) -> None:
        """Read one resource a run uses (a repo, a project), to prove the access (J5)."""
        raise LoginRejected(f"{self.kind} has no resource {resource!r} to read")

    @abstractmethod
    def is_available(self) -> bool:
        """True iff credentials are present for the company this
        provider was built for."""

    @abstractmethod
    def list_meetings(self, page: MeetingPage) -> List[Meeting]:
        """The meetings of one page of a date window. Which of them belong to the company is
        decided by the caller (`MeetingFilter`)."""

    @abstractmethod
    def search_meetings(self, *, query: str, max_count: int) -> List[Meeting]:
        """Keyword-match meetings whose title OR transcript contains
        `query`. Used by the JIT extractor to find meetings relevant
        to a ticket or PR title."""

    @abstractmethod
    def get_meeting(self, meeting_id: str) -> MeetingDetail:
        """Fetch one meeting with the full transcript populated.
        Concrete providers must implement this — previously had an
        empty-MeetingDetail default that silently masked unimplemented
        providers as `meeting_id == ""` (callers couldn't distinguish
        "not found" from "provider didn't implement single-fetch")."""
