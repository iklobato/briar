"""Mine the merged-PR history of one or more repos.

Surfaces the patterns + reviewer cadence agents should respect when
proposing changes. Modeled on
`claude-standalone/scans/pr_archaeology.py` condensed to a live agent
context blob (heavy duplicate-PR clustering is deferred to v2)."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from statistics import median
from typing import Any, Dict, List, Optional

from briar.extract._gh import GithubApi
from briar.extract._user_filter import (
    add_user_filter_arguments,
    apply_user_filter,
)
from briar.extract.base import ExtractedSection, KnowledgeExtractor


class ExtractPrArchaeology(KnowledgeExtractor):
    @staticmethod
    def _hours_between(start_iso: str, end_iso: str) -> Optional[float]:
        """Hours between two ISO-8601 timestamps; None on parse failure."""
        try:
            s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            e = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (e - s).total_seconds() / 3600.0

    name = "pr-archaeology"
    description = "merged-PR patterns, review focus, reviewer profiles"
    requires_github = True

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--pr-repo", action="append", default=[],
            help="GitHub repo to mine (owner/repo). Repeatable.",
        )
        parser.add_argument(
            "--pr-max", type=int, default=100,
            help="Max merged PRs per repo (default: 100)",
        )
        add_user_filter_arguments(parser, prefix="pr")

    def is_available(self, args: argparse.Namespace) -> bool:
        return bool(args.pr_repo) and bool(GithubApi.auth_token())

    def extract(self, args: argparse.Namespace) -> Optional[ExtractedSection]:
        per_repo: List[ExtractedSection] = []
        for repo in args.pr_repo:
            section = self._mine_repo(repo, args.pr_max, args)
            if section is not None:
                per_repo.append(section)
        if not per_repo:
            return None
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
    ) -> Optional[ExtractedSection]:
        path = (
            f"/repos/{repo}/pulls?state=closed&sort=updated&direction=desc"
        )
        pages_needed = max(1, (max_prs // 100) + 1)
        rows = GithubApi.get_paginated(path, max_pages=pages_needed)
        merged = [p for p in rows if p.get("merged_at") is not None]
        merged = apply_user_filter(merged, args, prefix="pr")[:max_prs]
        if not merged:
            return None

        cycle_hours = [
            h for h in (
                self._hours_between(p["created_at"], p["merged_at"])
                for p in merged
            )
            if h is not None
        ]
        reviewers: Counter = Counter()
        authors: Counter = Counter()
        for p in merged:
            authors[(p.get("user") or {}).get("login", "?")] += 1
            for r in (p.get("requested_reviewers") or []):
                reviewers[r.get("login", "?")] += 1

        data: Dict[str, Any] = {
            "repo": repo,
            "merged_pr_count": len(merged),
            "median_cycle_hours": (
                round(median(cycle_hours), 2) if cycle_hours else None
            ),
            "top_authors": authors.most_common(5),
            "top_reviewers": reviewers.most_common(5),
        }
        body_lines = [f"- merged PR sample: **{data['merged_pr_count']}**"]
        if data["median_cycle_hours"] is not None:
            body_lines.append(
                f"- median time-to-merge: **{data['median_cycle_hours']}h**"
            )
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
