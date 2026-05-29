"""Open PRs across the configured repos.

Helps agents avoid stepping on parallel work and signals which PRs
already have CI failures (potential rebase-conflict landmines).

Provider-agnostic: talks to a `RepositoryProvider`, not to GitHub
directly. ``--provider bitbucket`` routes the same logic onto Bitbucket
Cloud once `BitbucketProvider.list_pulls` is implemented."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.extract._provider import PullRequest
from briar.extract._user_filter import add_user_filter_arguments, apply_user_filter_objs
from briar.extract.base import ExtractedSection, RepoBackedExtractor

_MAX_PRS_PER_REPO = 25


class ExtractActiveWork(RepoBackedExtractor):
    name = "active-work"
    heading = "Active work"
    description = "open PRs across the configured repos"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--active-repo",
            action="append",
            default=[],
            help="Repository slug to scan for active work. Repeatable.",
        )
        add_user_filter_arguments(parser, prefix="active")

    _availability_arg = "active_repo"

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        sections = [self._scan_repo(repo, args, provider) for repo in args.active_repo]
        return ExtractedSection(
            title=f"Active work — {len(sections)} repo(s)",
            body=("Live snapshot of what's in flight. Agents must avoid " "touching files referenced in open PRs to prevent merge " "conflicts."),
            subsections=sections,
        )

    def _scan_repo(
        self,
        repo: str,
        args: argparse.Namespace,
        provider,
    ) -> ExtractedSection:
        prs: List[PullRequest] = provider.list_pulls(repo, state="open", max_count=_MAX_PRS_PER_REPO * 4)
        prs = apply_user_filter_objs(prs, args, prefix="active")
        pr_rows: List[Dict[str, Any]] = []
        for p in prs[:_MAX_PRS_PER_REPO]:
            pr_rows.append(
                {
                    "number": p.number,
                    "title": p.title[:80],
                    "user": p.author,
                    "draft": p.is_draft,
                    "head": p.head_ref,
                    "base": p.base_ref,
                    "review_comments": p.review_comment_count,
                }
            )
        lines = [
            f"- #{r['number']} {r['title']!r:80}  by={r['user']}" + ("  [draft]" if r["draft"] else "") + f"  comments={r['review_comments']}" for r in pr_rows
        ]
        return ExtractedSection(
            title=f"{repo} — {len(pr_rows)} open PR(s)",
            body="\n".join(lines) if lines else "_no open PRs_",
            data={"open_prs": pr_rows},
        )
