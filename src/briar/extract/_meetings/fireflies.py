"""Fireflies.ai `MeetingProvider`.

Fireflies exposes a single GraphQL endpoint at
``https://api.fireflies.ai/graphql``. Auth is a personal API key
passed as ``Authorization: Bearer <key>``. Implemented with stdlib
``urllib`` (same pattern as `LinearTracker`) so the base install
needs no new dependency.

The `transcripts(...)` query handles both time-window scans and
keyword search via the same shape — `list_meetings` passes
`fromDate`/`toDate`, `search_meetings` passes `keyword` + `scope:
ALL`. `transcript(id: ...)` returns one meeting with full sentences."""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from briar._http_retry import urlopen_with_retry
from briar.decorators import swallow_errors
from briar.env_vars import CredEnv
from briar.extract._meeting import Meeting, MeetingDetail, MeetingProvider

log = logging.getLogger(__name__)


_ENDPOINT = "https://api.fireflies.ai/graphql"

# Fireflies' `transcripts` `limit` argument caps at 50 per request.
# Single-page is enough for the digest sizes briar uses (default 25,
# operator can override up to 50). Pagination would be a follow-up.
_MAX_LIMIT = 50


class FirefliesMeetingProvider(MeetingProvider):
    kind = "fireflies"

    def __init__(self, *, company: str = "") -> None:
        self._company = company
        self._token = CredEnv.FIREFLIES_API_KEY.read(company=company) if company else ""

    def is_available(self) -> bool:
        return bool(self._token)

    @classmethod
    def required_env_vars(cls, company: str = "") -> List[str]:
        if not company:
            return []
        return [CredEnv.FIREFLIES_API_KEY.for_company(company)]

    @swallow_errors(default=[], message="fireflies list_meetings")
    def list_meetings(self, *, since_iso: str, until_iso: str, max_count: int, attendees: Optional[List[str]] = None) -> List[Meeting]:
        variables: Dict[str, Any] = {
            "fromDate": since_iso,
            "toDate": until_iso,
            "limit": min(max(max_count, 1), _MAX_LIMIT),
        }
        if attendees:
            # Fireflies accepts a `participants: [String]` filter — at
            # least one match wins, which matches our "attendee allow"
            # semantics (any-of, not all-of).
            variables["participants"] = list(attendees)
        result = self._gql(_LIST_QUERY, variables)
        nodes = (result.get("data") or {}).get("transcripts") or []
        return [self._to_meeting(node) for node in nodes if isinstance(node, dict)]

    @swallow_errors(default=[], message="fireflies search_meetings")
    def search_meetings(self, *, query: str, max_count: int) -> List[Meeting]:
        if not query.strip():
            return []
        variables: Dict[str, Any] = {
            # Truncate to Fireflies' documented 255-char keyword cap;
            # ticket titles + extras can easily exceed it. Truncation
            # keeps the highest-signal head of the query.
            "keyword": query.strip()[:255],
            # `scope: ALL` = match in title OR sentences (the body).
            "scope": "ALL",
            "limit": min(max(max_count, 1), _MAX_LIMIT),
        }
        result = self._gql(_LIST_QUERY, variables)
        nodes = (result.get("data") or {}).get("transcripts") or []
        return [self._to_meeting(node) for node in nodes if isinstance(node, dict)]

    @swallow_errors(default=None, message="fireflies get_meeting")
    def get_meeting(self, meeting_id: str) -> MeetingDetail:
        result = self._gql(_DETAIL_QUERY, {"id": meeting_id})
        node = (result.get("data") or {}).get("transcript")
        if not isinstance(node, dict):
            return super().get_meeting(meeting_id)
        meeting = self._to_meeting(node)
        sentences = node.get("sentences") or []
        transcript = _render_transcript(sentences)
        summary = node.get("summary") or {}
        topics = _as_str_list(summary.get("topics_discussed"))
        keywords = _as_str_list(summary.get("keywords"))
        return MeetingDetail(
            meeting=meeting,
            transcript=transcript,
            topics=topics,
            keywords=keywords,
        )

    # ---- internals --------------------------------------------------------

    def _gql(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        req = urllib.request.Request(
            _ENDPOINT,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen_with_retry(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("errors"):
            # See LinearTracker._gql for rationale — raise so @swallow_errors
            # surfaces the failure rather than silently returning data:None.
            raise RuntimeError(f"fireflies graphql errors: {body['errors']}")
        return body

    @staticmethod
    def _to_meeting(node: Dict[str, Any]) -> Meeting:
        summary = node.get("summary") or {}
        attendees_raw = node.get("meeting_attendees") or []
        attendees: List[str] = []
        for att in attendees_raw:
            if not isinstance(att, dict):
                continue
            email = str(att.get("email") or "").strip()
            name = str(att.get("displayName") or att.get("name") or "").strip()
            attendees.append(email or name)
        if not attendees:
            # Fall back to the flat `participants` list — older
            # Fireflies workspaces don't populate `meeting_attendees`.
            attendees = [str(p) for p in (node.get("participants") or []) if p]
        return Meeting(
            meeting_id=str(node.get("id") or ""),
            title=str(node.get("title") or "")[:200],
            started_at=_normalise_date(node.get("date")),
            duration_sec=_as_int(node.get("duration")),
            organizer=str(node.get("organizer_email") or node.get("host_email") or ""),
            attendees=[a for a in attendees if a],
            url=str(node.get("transcript_url") or ""),
            summary=str(summary.get("overview") or "")[:4000],
            action_items=_as_str_list(summary.get("action_items")),
        )


# `transcripts` returns a list. Same query shape for time-window AND
# keyword search — the caller picks which arguments to send.
_LIST_QUERY = """
query Transcripts(
  $keyword: String,
  $fromDate: DateTime,
  $toDate: DateTime,
  $limit: Int,
  $skip: Int,
  $participants: [String],
  $scope: TranscriptsQueryScope
) {
  transcripts(
    keyword: $keyword,
    fromDate: $fromDate,
    toDate: $toDate,
    limit: $limit,
    skip: $skip,
    participants: $participants,
    scope: $scope
  ) {
    id
    title
    date
    duration
    organizer_email
    host_email
    participants
    transcript_url
    meeting_attendees { displayName email name }
    summary { overview action_items }
  }
}
""".strip()


_DETAIL_QUERY = """
query Transcript($id: String!) {
  transcript(id: $id) {
    id
    title
    date
    duration
    organizer_email
    host_email
    participants
    transcript_url
    meeting_attendees { displayName email name }
    summary { overview action_items keywords topics_discussed }
    sentences { index speaker_name text start_time }
  }
}
""".strip()


def _render_transcript(sentences: List[Dict[str, Any]]) -> str:
    """Flatten Fireflies' sentence array into a single markdown block
    grouped by consecutive speaker turns. Cuts per-line speaker repeats
    so a 50-line uninterrupted Alice monologue prints `**Alice**:` once
    instead of 50 times."""
    lines: List[str] = []
    current_speaker = ""
    buffer: List[str] = []
    for s in sentences:
        if not isinstance(s, dict):
            continue
        speaker = str(s.get("speaker_name") or "Unknown")
        text = str(s.get("text") or "").strip()
        if not text:
            continue
        if speaker != current_speaker:
            if buffer:
                lines.append(f"**{current_speaker}**: " + " ".join(buffer))
                buffer = []
            current_speaker = speaker
        buffer.append(text)
    if buffer:
        lines.append(f"**{current_speaker}**: " + " ".join(buffer))
    return "\n\n".join(lines)


def _normalise_date(raw: Any) -> str:
    """Fireflies returns `date` as a Float (epoch ms). Normalise to
    ISO-8601 so the cross-vendor `Meeting.started_at` field is uniform
    with what a future provider might emit natively."""
    if raw is None:
        return ""
    try:
        # epoch-ms → seconds
        secs = float(raw) / 1000.0
    except (TypeError, ValueError):
        # Some payloads may already be ISO strings; pass them through.
        return str(raw)
    return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()


def _as_int(raw: Any) -> int:
    try:
        return int(float(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _as_str_list(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        # Fireflies emits action_items as a newline-delimited string
        # in some workspace tiers, a list in others. Handle both.
        return [line.strip(" -•\t") for line in raw.splitlines() if line.strip()]
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []
