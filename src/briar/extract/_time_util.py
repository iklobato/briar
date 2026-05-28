"""Time helpers shared by extract verbs.

`hours_between` was duplicated verbatim in `pr_archaeology.py` and
`ticket_archaeology.py` — both also redefined the same
``UNPARSABLE_HOURS = -1.0`` sentinel. Single source of truth here so
adding a third caller doesn't fork the parser."""

from __future__ import annotations

from datetime import datetime


# Sentinel returned when an ISO-8601 string fails to parse. Callers
# filter `< 0` rows out before averaging — same convention both
# pr-archaeology and ticket-archaeology used independently.
UNPARSABLE_HOURS: float = -1.0


def hours_between(start_iso: str, end_iso: str) -> float:
    """Hours between two ISO-8601 timestamps. Returns
    ``UNPARSABLE_HOURS`` (-1.0) on a parse error so callers can filter
    rather than crash.

    The ``Z`` → ``+00:00`` swap mirrors `datetime.fromisoformat`'s
    pre-3.11 inability to read the Zulu form."""
    try:
        s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except ValueError:
        return UNPARSABLE_HOURS
    return (e - s).total_seconds() / 3600.0
