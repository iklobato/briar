"""Open PRs idle beyond a threshold — the review backlog.

`active-work` already lists every open PR; this extractor narrows to the
ones that have been open long enough to be a review-backlog smell, so
agents know which long-lived branches to avoid colliding with.

Age is measured from PR *creation*, not last activity — the
`RepositoryProvider` list endpoint doesn't expose a last-touched
timestamp. The body labels each PR ``open <N>d`` to make that explicit.

Provider-agnostic: talks to a `RepositoryProvider`, not GitHub directly.
``--provider bitbucket`` routes the same logic onto Bitbucket Cloud once
`BitbucketProvider.list_pulls` is implemented."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any, Dict, List

from briar.extract._provider import PullRequest
from briar.extract._time_util import UNPARSABLE_HOURS, hours_between
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section

_MAX_LISTED = 20


class ExtractStalePrs(RepoBackedExtractor):
    name = "stale-prs"
    heading = "Stale PRs"
    description = "open PRs idle beyond a threshold — review backlog to avoid colliding with"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--stale-repo",
            action="append",
            default=[],
            help="Repository slug to scan for stale PRs. Repeatable.",
        )
        parser.add_argument(
            "--stale-max",
            type=int,
            default=100,
            help="Max open PRs to fetch per repo (default: 100)",
        )
        parser.add_argument(
            "--stale-days",
            type=int,
            default=14,
            help="A PR open longer than this many days is stale (default: 14)",
        )

    _availability_arg = "stale_repo"

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.stale_repo:
            section = self._scan_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"Stale PRs — {len(per_repo)} repo(s)",
            body=(
                "Open PRs that have lingered past the staleness threshold "
                "(age measured from PR creation, labelled `open <N>d`). "
                "These are the review-backlog branches agents should avoid "
                "colliding with."
            ),
            subsections=per_repo,
        )

    def _scan_repo(
        self,
        repo: str,
        args: argparse.Namespace,
        provider,
    ) -> ExtractedSection:
        open_prs: List[PullRequest] = provider.list_pulls(repo, state="open", max_count=args.stale_max)
        if not open_prs:
            return empty_section()

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        aged: List[tuple[PullRequest, float]] = []
        for p in open_prs:
            hours = hours_between(p.created_at, now_iso)
            if hours == UNPARSABLE_HOURS or hours < 0:
                continue
            aged.append((p, hours / 24.0))

        stale = [(p, age) for p, age in aged if age > args.stale_days]
        if not stale:
            return empty_section()

        stale.sort(key=lambda pa: pa[1], reverse=True)

        stale_rows: List[Dict[str, Any]] = [
            {
                "number": p.number,
                "title": p.title[:80],
                "author": p.author,
                "age_days": round(age, 1),
                "is_draft": p.is_draft,
            }
            for p, age in stale[:_MAX_LISTED]
        ]
        data = {
            "repo": repo,
            "open_pr_count": len(open_prs),
            "stale_pr_count": len(stale),
            "stale_threshold_days": args.stale_days,
            "stale_prs": stale_rows,
        }
        body_lines = [f"- **{len(stale)}** of {len(open_prs)} open PR(s) stale (> {args.stale_days}d):"]
        for r in stale_rows:
            draft = "  [draft]" if r["is_draft"] else ""
            body_lines.append(f"  - #{r['number']} {r['title']!r}  by={r['author']}  open {r['age_days']}d{draft}")
        return ExtractedSection(
            title=f"{repo} — {len(stale)} stale PR(s)",
            body="\n".join(body_lines),
            data=data,
        )
