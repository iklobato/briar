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
from dataclasses import dataclass, field
from typing import ClassVar, List, Optional

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
    header lines for one meeting. Two callers — `meeting_context`
    (uses `attendee_cap=12`, no overflow suffix) and `meeting_digest`
    (uses `attendee_cap=8` with `+N more` suffix) — shared the same
    six lines verbatim before this helper existed."""
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

    @abstractmethod
    def is_available(self) -> bool:
        """True iff credentials are present for the company this
        provider was built for."""

    @abstractmethod
    def list_meetings(self, *, since_iso: str, until_iso: str, max_count: int, attendees: Optional[List[str]] = None) -> List[Meeting]:
        """List meetings within a date window. `since_iso` / `until_iso`
        are ISO-8601 timestamps. `attendees` (optional) filters to
        meetings where at least one attendee email matches; empty
        list = no filter. Most-recent first."""

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

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        """Env vars the doctor reports as required for this provider
        in the context of `company`. Mirrors `TrackerProvider`."""
        return []
