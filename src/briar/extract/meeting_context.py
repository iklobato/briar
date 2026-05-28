"""Task-scoped: fetch transcript(s) relevant to ONE specific task.

Invoked by `briar agent implement` and `briar agent prfix` at agent-
invocation time. Output is spliced into that single agent run's
system prompt — it does NOT go into the per-company knowledge blob.

Two modes (one wins per invocation):

  ``--meeting-key <id>``       fetch ONE specific meeting by ID. Use
                               when the operator (or the runbook)
                               already knows which standup is relevant
                               — e.g. ticket links to the recording.

  ``--meeting-query <text>``   keyword search. Pulls the top-K
                               meetings whose title OR transcript
                               matches ``text``. The agent CLI defaults
                               this to the ticket title (implement) or
                               PR title (prfix) when neither flag is set
                               explicitly."""

from __future__ import annotations

import argparse
import logging
from typing import List

from briar.extract._meeting import Meeting, MeetingDetail, MeetingProvider, render_meeting_header
from briar.extract.base import ExtractedSection, TaskScopedMeetingExtractor, empty_section

log = logging.getLogger(__name__)


_DEFAULT_SEARCH_K = 3
_DEFAULT_MAX_BYTES = 50_000


class FetchMeetingContext(TaskScopedMeetingExtractor):
    name = "meeting-context"
    heading = "Meeting context"
    description = "Transcript(s) relevant to one ticket or PR"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--meeting-key",
            default="",
            help="Specific meeting ID to fetch. Takes precedence over --meeting-query.",
        )
        parser.add_argument(
            "--meeting-query",
            default="",
            help="Keyword search. Pulls top-K matching meetings (title + transcript).",
        )
        parser.add_argument(
            "--meeting-top-k",
            type=int,
            default=_DEFAULT_SEARCH_K,
            help=f"Max meetings to fetch in search mode (default: {_DEFAULT_SEARCH_K})",
        )
        parser.add_argument(
            "--meeting-max-bytes",
            type=int,
            default=_DEFAULT_MAX_BYTES,
            help=f"Per-meeting transcript byte cap (default: {_DEFAULT_MAX_BYTES})",
        )

    def fetch(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._meeting(args)
        if not provider.is_available():
            log.info("meeting-context: provider not available — skipping")
            return empty_section()

        meeting_key = (getattr(args, "meeting_key", "") or "").strip()
        meeting_query = (getattr(args, "meeting_query", "") or "").strip()
        top_k = max(int(getattr(args, "meeting_top_k", _DEFAULT_SEARCH_K)), 1)
        max_bytes = max(int(getattr(args, "meeting_max_bytes", _DEFAULT_MAX_BYTES)), 1024)

        if meeting_key:
            return self._fetch_one(provider, meeting_key, max_bytes)
        if meeting_query:
            return self._fetch_by_query(provider, meeting_query, top_k, max_bytes)
        log.info("meeting-context: neither --meeting-key nor --meeting-query passed — skipping")
        return empty_section()

    def _fetch_one(self, provider: MeetingProvider, meeting_id: str, max_bytes: int) -> ExtractedSection:
        detail = provider.get_meeting(meeting_id)
        if detail is None or not detail.meeting.meeting_id:
            log.warning("meeting-context: %s not found or empty", meeting_id)
            return empty_section()
        body = _render_detail(detail, max_bytes)
        return ExtractedSection(
            title=f"Meeting context — {detail.meeting.title or detail.meeting.meeting_id}",
            body=body,
            data={
                "meeting_id": detail.meeting.meeting_id,
                "started_at": detail.meeting.started_at,
                "attendees": detail.meeting.attendees,
            },
        )

    def _fetch_by_query(self, provider: MeetingProvider, query: str, top_k: int, max_bytes: int) -> ExtractedSection:
        meetings: List[Meeting] = provider.search_meetings(query=query, max_count=top_k)
        if not meetings:
            log.info("meeting-context: no matches for query=%r", query)
            return empty_section()

        # Hydrate each matched meeting's transcript. Each get_meeting
        # is wrapped in swallow_errors at the adapter — a single
        # failure returns None and we drop it, never abort the whole
        # fetch (the agent run is more valuable than 100% recall).
        details: List[MeetingDetail] = []
        for meeting in meetings:
            detail = provider.get_meeting(meeting.meeting_id)
            if detail is not None and detail.meeting.meeting_id:
                details.append(detail)
        if not details:
            return empty_section()

        per_meeting_budget = max(max_bytes // len(details), 2_000)
        parts: List[str] = [
            f"_Top {len(details)} match(es) for query `{query[:120]}`. Treat decisions captured here as binding._",
            "",
        ]
        for detail in details:
            parts.append(
                f"### {detail.meeting.title or detail.meeting.meeting_id}  ({detail.meeting.started_at[:10] if detail.meeting.started_at else 'unknown'})"
            )
            parts.append("")
            parts.append(_render_detail(detail, per_meeting_budget))
            parts.append("")

        data = {
            "query": query,
            "match_count": len(details),
        }
        return ExtractedSection(
            title=f"Meeting context — {len(details)} match(es) for {query[:60]!r}",
            body="\n".join(parts),
            data=dict(data),
        )


def _render_detail(detail: MeetingDetail, max_bytes: int) -> str:
    """Markdown render of one MeetingDetail with the transcript capped
    at `max_bytes`. Header + summary + action items always render in
    full; the transcript is what gets truncated."""
    m = detail.meeting
    lines: List[str] = render_meeting_header(m, attendee_cap=12, show_more_suffix=False)
    if detail.topics:
        lines.append(f"**Topics**: {', '.join(detail.topics[:10])}")
    lines.append("")
    if m.summary:
        lines.append("**Summary**:")
        lines.append(m.summary)
        lines.append("")
    if m.action_items:
        lines.append(f"**Action items** ({len(m.action_items)}):")
        for item in m.action_items:
            lines.append(f"- {item}")
        lines.append("")
    if detail.transcript:
        lines.append("**Transcript**:")
        transcript = detail.transcript
        encoded = transcript.encode("utf-8")
        if len(encoded) > max_bytes:
            # Truncate to the byte budget. errors="replace" inserts
            # U+FFFD at boundary cuts so multi-byte characters (CJK,
            # emoji) don't silently disappear — was errors="ignore"
            # which dropped them invisibly.
            log.info("meeting %s transcript truncated: %d -> %d bytes",
                     m.meeting_id, len(encoded), max_bytes)
            truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
            transcript = truncated + f"\n\n_…transcript truncated at {max_bytes} bytes; fetch full via `--meeting-key {m.meeting_id}`._"
        lines.append(transcript)
    return "\n".join(lines)
