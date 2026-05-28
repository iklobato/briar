"""Meeting provider registry.

Symmetric to `_trackers/` and `_providers/`. Adding a new vendor
(Otter, Granola, Read.ai, …) = one entry in `MEETINGS` below.

The Phase 13 demotion removed the static-only `MeetingProviderRegistry`
namespace class — `meeting_kinds()` and `make_meeting()` are module
functions now, matching the registry-as-data pattern used elsewhere."""

from __future__ import annotations

from typing import Dict, Tuple, Type

from briar._registry import build_registry
from briar.errors import CliError
from briar.extract._meeting import MeetingProvider
from briar.extract._meetings.fireflies import FirefliesMeetingProvider

MEETINGS: Dict[str, Type[MeetingProvider]] = build_registry(
    (FirefliesMeetingProvider,),
    kind="meeting provider",
    name_attr="kind",
)


def meeting_kinds() -> Tuple[str, ...]:
    return tuple(MEETINGS.keys())


def make_meeting(kind: str, company: str = "") -> MeetingProvider:
    provider_cls = MEETINGS.get(kind)
    if provider_cls is None:
        known = ", ".join(sorted(MEETINGS.keys()))
        raise CliError(f"unknown meeting provider {kind!r}; known: {known}")
    return provider_cls(company=company)


__all__ = ["MEETINGS", "MeetingProvider", "meeting_kinds", "make_meeting"]
