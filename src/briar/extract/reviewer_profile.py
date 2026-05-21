"""Mine each top-reviewer's review behaviour per repo.

Surfaces patterns the engineer/pr-fixer archetypes should match:
which reviewers leave the most comments, what files they touch, the
typical request volume per PR. Lets an agent calibrate "how much
review depth does this reviewer expect" instead of guessing.

Provider-agnostic: same RepositoryProvider contract as
pr-archaeology. Falls back gracefully on providers that don't expose
review comments via the `list_pr_comments` verb."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from typing import Any, Dict, List

from briar.extract._provider import PullRequest, ReviewComment
from briar.extract.base import EMPTY_SECTION, ExtractedSection, RepoBackedExtractor


class ExtractReviewerProfile(RepoBackedExtractor):
    name = "reviewer-profile"
    description = "per-reviewer comment cadence, file hotspots, common asks"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--reviewer-repo",
            action="append",
            default=[],
            help="Repository slug to profile reviewers for. Repeatable.",
        )
        parser.add_argument(
            "--reviewer-pr-sample",
            type=int,
            default=20,
            help="How many recent merged PRs to sample per repo (default: 20)",
        )
        parser.add_argument(
            "--reviewer-top-n",
            type=int,
            default=5,
            help="How many top reviewers to profile (default: 5)",
        )

    def is_available(self, args: argparse.Namespace) -> bool:
        if not args.reviewer_repo:
            return False
        try:
            provider = self._provider(args)
        except Exception:  # noqa: BLE001
            return False
        return provider.is_available()

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.reviewer_repo:
            section = self._profile_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return EMPTY_SECTION
        return ExtractedSection(
            title=f"Reviewer profiles — {len(per_repo)} repo(s)",
            body=(
                "Per-reviewer behaviour mined from recent merged PRs. "
                "Agents should match the bar of the most-likely reviewer "
                "for the touched files — not the project median."
            ),
            subsections=per_repo,
        )

    def _profile_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        sample = provider.list_pulls(repo, state="merged", max_count=args.reviewer_pr_sample)
        if not sample:
            return EMPTY_SECTION

        # Aggregate: per-reviewer, total comments + which files they
        # commented on + sample of their actual comment bodies.
        per_reviewer_comments: Dict[str, int] = Counter()
        per_reviewer_files: Dict[str, Counter] = defaultdict(Counter)
        per_reviewer_samples: Dict[str, List[str]] = defaultdict(list)
        prs_reviewed: Dict[str, int] = Counter()

        for pr in sample:
            comments: List[ReviewComment] = provider.list_pr_comments(repo, pr.number)
            reviewers_on_this_pr: set = set()
            for c in comments:
                if not c.author or c.author == pr.author:
                    continue  # skip self-comments
                per_reviewer_comments[c.author] += 1
                if c.file_path:
                    per_reviewer_files[c.author][c.file_path] += 1
                # Keep a short sample of actual comment text per reviewer.
                if len(per_reviewer_samples[c.author]) < 3 and len(c.body) > 20:
                    per_reviewer_samples[c.author].append(c.body[:200])
                reviewers_on_this_pr.add(c.author)
            for r in reviewers_on_this_pr:
                prs_reviewed[r] += 1

        if not per_reviewer_comments:
            return EMPTY_SECTION

        top_reviewers = per_reviewer_comments.most_common(args.reviewer_top_n)
        body_parts: List[str] = [
            f"Sample: {len(sample)} merged PRs",
            f"Active reviewers: {len(per_reviewer_comments)}",
            "",
        ]
        data_rows: List[Dict[str, Any]] = []
        for reviewer, total_comments in top_reviewers:
            prs = prs_reviewed.get(reviewer, 0)
            avg_per_pr = round(total_comments / prs, 1) if prs else 0.0
            top_files = [path for path, _ in per_reviewer_files[reviewer].most_common(3)]
            body_parts.append(f"### {reviewer}")
            body_parts.append(f"- PRs reviewed: **{prs}** / comments left: **{total_comments}** (avg **{avg_per_pr}**/PR)")
            if top_files:
                body_parts.append(f"- Hot files: {', '.join(top_files)}")
            samples = per_reviewer_samples.get(reviewer, [])
            if samples:
                body_parts.append(f"- Sample asks (truncated):")
                for s in samples:
                    body_parts.append(f"  - _{s}_")
            body_parts.append("")
            data_rows.append(
                {
                    "reviewer": reviewer,
                    "prs_reviewed": prs,
                    "comments": total_comments,
                    "avg_comments_per_pr": avg_per_pr,
                    "top_files": top_files,
                }
            )
        return ExtractedSection(
            title=repo,
            body="\n".join(body_parts),
            data={"reviewers": data_rows, "pr_sample_size": len(sample)},
        )
