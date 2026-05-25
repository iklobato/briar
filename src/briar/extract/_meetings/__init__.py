"""Meeting provider registry — Strategy + Factory.

Symmetric to `_trackers/` and `_providers/`. Adding a new vendor
(Otter, Granola, Read.ai, …) = one module + one entry; zero extractor
edits, zero archetype edits."""

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


class MeetingProviderRegistry:
    """Factory + introspection. Static-only — provider construction is
    cheap (env-var read + a typed client), so re-creating per extractor
    call keeps the surface dependency-free."""

    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(MEETINGS.keys())

    @classmethod
    def make(cls, kind: str, company: str = "") -> MeetingProvider:
        provider_cls = MEETINGS.get(kind)
        if provider_cls is None:
            known = ", ".join(sorted(MEETINGS.keys()))
            raise CliError(f"unknown meeting provider {kind!r}; known: {known}")
        return provider_cls(company=company)


make_meeting = MeetingProviderRegistry.make


__all__ = ["MEETINGS", "MeetingProvider", "MeetingProviderRegistry", "make_meeting"]
