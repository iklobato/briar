"""Mine the merged-PR history of one or more repos.

Surfaces the patterns + reviewer cadence agents should respect when
proposing changes. Modeled on
`claude-standalone/scans/pr_archaeology.py` condensed to a live agent
context blob (heavy duplicate-PR clustering is deferred to v2).

Provider-agnostic: this extractor talks to a `RepositoryProvider`,
not to GitHub directly. Setting ``--provider bitbucket`` in the
runbook routes the same logic onto Bitbucket Cloud once
`BitbucketProvider.list_pulls` is implemented."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from statistics import median
from typing import Any, Dict, List

from briar.extract._provider import PullRequest
from briar.extract._user_filter import (
    add_user_filter_arguments,
    apply_user_filter_objs,
)
from briar.extract._time_util import UNPARSABLE_HOURS, hours_between
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section


class ExtractPrArchaeology(RepoBackedExtractor):
    # Re-exported as class attrs so existing call sites that used
    # `cls._hours_between` / `cls.UNPARSABLE_HOURS` keep working.
    UNPARSABLE_HOURS = UNPARSABLE_HOURS
    _hours_between = staticmethod(hours_between)

    name = "pr-archaeology"
    heading = "PR archaeology"
    description = "merged-PR patterns, review focus, reviewer profiles"
    requires_github = True  # legacy flag — kept for back-compat; new gate is requires_repository_provider

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--pr-repo",
            action="append",
            default=[],
            help="Repository slug to mine (e.g. owner/repo). Repeatable.",
        )
        parser.add_argument(
            "--pr-max",
            type=int,
            default=100,
            help="Max merged PRs per repo (default: 100)",
        )
        add_user_filter_arguments(parser, prefix="pr")

    def is_available(self, args: argparse.Namespace) -> bool:
        if not args.pr_repo:
            return False
        try:
            provider = self._provider(args)
        except Exception:  # noqa: BLE001
            return False
        return provider.is_available()

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.pr_repo:
            section = self._mine_repo(repo, args.pr_max, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"PR archaeology — {len(per_repo)} repo(s)",
            body=(
                "Patterns from the most recent merged PRs. Agents should "
                "match the established conventions (review focus, file "
                "paths touched most, reviewer cadence)."
            ),
            subsections=per_repo,
        )

    def _mine_repo(
        self,
        repo: str,
        max_prs: int,
        args: argparse.Namespace,
        provider,
    ) -> ExtractedSection:
        merged: List[PullRequest] = provider.list_pulls(repo, state="merged", max_count=max_prs)
        merged = apply_user_filter_objs(merged, args, prefix="pr")
        if not merged:
            return empty_section()

        cycle_hours = [h for h in (self._hours_between(p.created_at, p.merged_at) for p in merged) if h >= 0]
        reviewers: Counter = Counter()
        authors: Counter = Counter()
        for p in merged:
            authors[p.author or "?"] += 1
            for r in p.requested_reviewers:
                reviewers[r or "?"] += 1

        data: Dict[str, Any] = {
            "repo": repo,
            "merged_pr_count": len(merged),
            "median_cycle_hours": (round(median(cycle_hours), 2) if cycle_hours else None),
            "top_authors": authors.most_common(5),
            "top_reviewers": reviewers.most_common(5),
        }
        body_lines = [f"- merged PR sample: **{data['merged_pr_count']}**"]
        if data["median_cycle_hours"] is not None:
            body_lines.append(f"- median time-to-merge: **{data['median_cycle_hours']}h**")
        if data["top_authors"]:
            top = ", ".join(f"{u}({n})" for u, n in data["top_authors"])
            body_lines.append(f"- top authors: {top}")
        if data["top_reviewers"]:
            top = ", ".join(f"{u}({n})" for u, n in data["top_reviewers"])
            body_lines.append(f"- requested reviewers: {top}")
        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data=data,
        )
