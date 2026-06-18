"""Release cadence — how often a repo ships.

Walks a repo's published releases / tags and derives the shipping
rhythm an agent should be aware of: how many releases exist in the
sample, when the most recent one landed, the median number of days
between consecutive releases, and how many of them were prereleases.

Provider-agnostic: talks to a `RepositoryProvider` via
``list_releases`` (empty default on providers without a release
concept, so the section simply renders empty there)."""

from __future__ import annotations

import argparse
from datetime import datetime
from statistics import median
from typing import Any, Dict, List, Optional

from briar.extract._provider import Release
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section


def _parse(ts: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp, returning None when unparseable.

    Releases occasionally carry an empty or malformed `created_at`
    (draft tags, vendor quirks) — those are skipped from the gap math
    rather than crashing the whole section."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


class ExtractReleaseCadence(RepoBackedExtractor):
    name = "release-cadence"
    heading = "Release cadence"
    description = "how often the repo ships — release frequency and recency"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--release-repo",
            action="append",
            default=[],
            help="Repository slug to analyse. Repeatable.",
        )
        parser.add_argument(
            "--release-max",
            type=int,
            default=100,
            help="Max releases to inspect per repo (default: 100)",
        )

    _availability_arg = "release_repo"

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.release_repo:
            section = self._analyse_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"Release cadence — {len(per_repo)} repo(s)",
            body=(
                "How often each repo ships. Use the median gap and the "
                "most-recent release to judge whether a change is likely "
                "to ride an imminent release or sit unreleased for a while."
            ),
            subsections=per_repo,
        )

    def _analyse_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        releases: List[Release] = provider.list_releases(repo, max_count=args.release_max)
        if not releases:
            return empty_section()

        # ISO 8601 strings sort lexicographically; empties sort to the
        # end (most-recent first) so a missing date never masquerades as
        # the latest release.
        ordered = sorted(releases, key=lambda r: r.created_at or "", reverse=True)

        parsed = [dt for dt in (_parse(r.created_at) for r in ordered) if dt is not None]
        gaps = [(parsed[i] - parsed[i + 1]).days for i in range(len(parsed) - 1)]
        median_days_between = round(median(gaps), 1) if gaps else None

        latest = ordered[0]
        last_release = {"tag": latest.tag, "created_at": latest.created_at}
        prerelease_count = sum(1 for r in releases if r.is_prerelease)

        data: Dict[str, Any] = {
            "repo": repo,
            "release_count": len(releases),
            "last_release": last_release,
            "median_days_between": median_days_between,
            "prerelease_count": prerelease_count,
        }
        body_lines = [f"- releases sampled: **{data['release_count']}**"]
        body_lines.append(f"- latest: `{last_release['tag']}` ({last_release['created_at']})")
        if median_days_between is not None:
            body_lines.append(f"- median days between releases: **{median_days_between}**")
        body_lines.append(f"- prereleases: **{prerelease_count}**")
        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data=data,
        )
