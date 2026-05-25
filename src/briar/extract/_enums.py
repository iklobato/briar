"""Closed enumerations for the extract subsystem.

Per ARCHITECTURE_MAP.md §21: enums for closed domain sets, registries
for open plug-in spaces.
"""
from __future__ import annotations

from enum import Enum


class MeetingExtractMode(str, Enum):
    """Which fetch path produced a meeting ExtractedSection.data payload.

    Used as the `mode` field in MeetingExtractedData so consumers can
    discriminate which other fields are present (by-id populates
    `meeting_id`/`attendees`; search populates `query`/`match_count`;
    digest populates `title` per subsection).
    """

    BY_ID = "by-id"
    SEARCH = "search"
    DIGEST = "digest"
