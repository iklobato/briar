"""Tracker provider registry — same Strategy + Factory as
`_providers/`. Adding a new tracker (Linear, ClickUp, Asana, …) =
one module + one entry."""

from __future__ import annotations

from typing import Dict, Tuple, Type

from briar._registry import build_registry
from briar.errors import CliError
from briar.extract._tracker import TrackerProvider
from briar.extract._trackers.bitbucket import BitbucketIssuesTracker
from briar.extract._trackers.github_issues import GithubIssuesTracker
from briar.extract._trackers.jira import JiraTracker
from briar.extract._trackers.linear import LinearTracker


TRACKERS: Dict[str, Type[TrackerProvider]] = build_registry(
    (JiraTracker, GithubIssuesTracker, BitbucketIssuesTracker, LinearTracker),
    kind="tracker provider",
    name_attr="kind",
)


class TrackerRegistry:
    """Factory + introspection."""

    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(TRACKERS.keys())

    @classmethod
    def make(cls, kind: str, company: str = "") -> TrackerProvider:
        tracker_cls = TRACKERS.get(kind)
        if tracker_cls is None:
            known = ", ".join(sorted(TRACKERS.keys()))
            raise CliError(f"unknown tracker {kind!r}; known: {known}")
        return tracker_cls(company=company)


make_tracker = TrackerRegistry.make


__all__ = ["TRACKERS", "TrackerProvider", "TrackerRegistry", "make_tracker"]
