"""Surface PR-hygiene signals for one or more repos.

Complements `pr-archaeology` (which mines cadence + reviewer profiles)
with size-distribution and review-quality smells: how big PRs tend to
be, how often a large PR slips through, how often a PR merges with zero
review comments (a rubber-stamp), and how long the first review takes.

Provider-agnostic like the other `RepoBackedExtractor`s: it talks to a
`RepositoryProvider`, never to GitHub directly.

Two cost notes baked into the flags:
  * the diffstat per-PR detail is only on the single-PR GET
    (`get_pull`), absent from `list_pulls` — so size/first-review stats
    come from a *capped* sample (`--prhygiene-diffstat-sample`) to bound
    the round-trips; the cap is reported in `data`, never hidden.
  * the rubber-stamp rate, by contrast, reads `review_comment_count`
    which `list_pulls` already carries, so it spans the FULL merged set."""

from __future__ import annotations

import argparse
import logging
from statistics import median
from typing import Any, Dict, List

from briar.extract._provider import PullRequest
from briar.extract._time_util import hours_between
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section

log = logging.getLogger(__name__)


class ExtractPrHygiene(RepoBackedExtractor):
    name = "pr-hygiene"
    heading = "PR hygiene"
    description = "PR size distribution, large-PR rate, rubber-stamp rate, time-to-first-review"
    requires_github = True

    _availability_arg = "prhygiene_repo"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--prhygiene-repo",
            action="append",
            default=[],
            help="Repository slug to inspect (e.g. owner/repo). Repeatable.",
        )
        parser.add_argument(
            "--prhygiene-max",
            type=int,
            default=100,
            help="Max merged PRs to consider for the rubber-stamp rate (default: 100)",
        )
        parser.add_argument(
            "--prhygiene-diffstat-sample",
            type=int,
            default=30,
            help="Cap on per-PR get_pull hydrations for size/first-review stats (default: 30)",
        )
        parser.add_argument(
            "--prhygiene-large-loc",
            type=int,
            default=400,
            help="LOC threshold (additions + deletions) for a 'large' PR (default: 400)",
        )

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.prhygiene_repo:
            section = self._inspect_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"PR hygiene — {len(per_repo)} repo(s)",
            body=(
                "Size-distribution and review-quality smells from recent "
                "merged PRs. Agents should keep PRs small and expect a real "
                "review — large PRs and zero-comment merges are the smells "
                "to avoid."
            ),
            subsections=per_repo,
        )

    def _inspect_repo(
        self,
        repo: str,
        args: argparse.Namespace,
        provider,
    ) -> ExtractedSection:
        merged: List[PullRequest] = provider.list_pulls(repo, state="merged", max_count=args.prhygiene_max)
        if not merged:
            return empty_section()

        # Rubber-stamp rate spans the FULL merged set — review_comment_count
        # is already carried by list_pulls, no per-PR hydration needed.
        no_review = sum(1 for p in merged if p.review_comment_count == 0)
        rubber_stamp_rate = round(no_review / len(merged), 2)

        # Size + first-review stats come from a capped, hydrated sample —
        # get_pull is one round-trip each, so bound it.
        sample_size = min(args.prhygiene_diffstat_sample, len(merged))
        sample = merged[:sample_size]
        if sample_size < len(merged):
            log.info(
                "pr-hygiene %s: size stats from a capped sample of %d of %d merged PRs",
                repo,
                sample_size,
                len(merged),
            )

        sizes: List[int] = []
        first_review_hours: List[float] = []
        for pr in sample:
            hydrated = provider.get_pull(repo, pr.number)
            sizes.append(hydrated.additions + hydrated.deletions)
            comments = provider.list_pr_comments(repo, pr.number)
            created = [c.created_at for c in comments if c.created_at]
            if created:
                hrs = hours_between(pr.created_at, min(created))
                if hrs >= 0:
                    first_review_hours.append(hrs)

        sorted_sizes = sorted(sizes)
        median_pr_size = round(median(sizes)) if sizes else None
        p90_pr_size = sorted_sizes[int(0.9 * (len(sorted_sizes) - 1))] if sorted_sizes else None
        large = sum(1 for s in sizes if s > args.prhygiene_large_loc)
        large_pr_rate = round(large / len(sizes), 2) if sizes else None
        median_hours_to_first_review = round(median(first_review_hours), 2) if first_review_hours else None

        data: Dict[str, Any] = {
            "repo": repo,
            "merged_pr_count": len(merged),
            "diffstat_sample_size": sample_size,
            "median_pr_size": median_pr_size,
            "p90_pr_size": p90_pr_size,
            "large_pr_rate": large_pr_rate,
            "rubber_stamp_rate": rubber_stamp_rate,
            "median_hours_to_first_review": median_hours_to_first_review,
        }

        body_lines = [f"- merged PR sample: **{data['merged_pr_count']}** " f"(size stats from {sample_size})"]
        if median_pr_size is not None:
            body_lines.append(f"- median PR size: **{median_pr_size} LOC**")
        if p90_pr_size is not None:
            body_lines.append(f"- p90 PR size: **{p90_pr_size} LOC**")
        if large_pr_rate is not None:
            body_lines.append(f"- large-PR rate (>{args.prhygiene_large_loc} LOC): **{large_pr_rate}**")
        body_lines.append(f"- rubber-stamp rate (0 review comments): **{rubber_stamp_rate}**")
        if median_hours_to_first_review is not None:
            body_lines.append(f"- median time-to-first-review: **{median_hours_to_first_review}h**")

        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data=data,
        )
