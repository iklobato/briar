"""Type-only contracts for ExtractedSection.data payloads.

Per ARCHITECTURE_MAP.md §17 step 5 + §21:

The `ExtractedSection.data` field is `Dict[str, Any]` for wire
compatibility (consumers across the codebase rely on it being a
plain dict). The TypedDicts below are *internal* contracts: they
document what each producer puts in there and let type checkers
narrow consumer access without changing the runtime shape.

`total=False` because different producers populate different subsets
keyed on `mode`.
"""
from __future__ import annotations

from typing import List, TypedDict

from briar.extract._enums import MeetingExtractMode


class MeetingExtractedData(TypedDict, total=False):
    """Shape of `ExtractedSection.data` populated by meeting-* extractors.

    Discriminator: `mode`. Each producer sets a different subset:

    - `MeetingExtractMode.BY_ID`:   meeting_id, started_at, attendees
    - `MeetingExtractMode.SEARCH`:  query, match_count
    - `MeetingExtractMode.DIGEST`:  meeting_count, since_iso, until_iso
                                    (subsections carry per-meeting title)

    Consumers should use `.get(...)`, not `[...]`.
    """

    mode: MeetingExtractMode
    # by-id mode fields
    meeting_id: str
    started_at: str          # ISO-8601
    attendees: List[str]
    # search mode fields
    query: str
    match_count: int
    # digest mode fields
    meeting_count: int
    since_iso: str
    until_iso: str
    # digest subsection field
    title: str
