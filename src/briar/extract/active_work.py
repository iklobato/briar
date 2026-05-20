"""Open PRs across the configured repos.

Helps agents avoid stepping on parallel work and signals which PRs
already have CI failures (potential rebase-conflict landmines)."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.extract._gh import GithubApi
from briar.extract._user_filter import (
    add_user_filter_arguments,
    apply_user_filter,
)
from briar.extract.base import ExtractedSection, KnowledgeExtractor


_MAX_PRS_PER_REPO = 25


class ExtractActiveWork(KnowledgeExtractor):
    name = "active-work"
    description = "open PRs across the configured repos"
    requires_github = True

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--active-repo",
            action="append",
            default=[],
            help="GitHub repo to scan for active work. Repeatable.",
        )
        add_user_filter_arguments(parser, prefix="active")

    def is_available(self, args: argparse.Namespace) -> bool:
        return bool(args.active_repo) and bool(GithubApi.auth_token())

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        sections = [self._scan_repo(repo, args) for repo in args.active_repo]
        return ExtractedSection(
            title=f"Active work — {len(sections)} repo(s)",
            body=("Live snapshot of what's in flight. Agents must avoid " "touching files referenced in open PRs to prevent merge " "conflicts."),
            subsections=sections,
        )

    def _scan_repo(
        self,
        repo: str,
        args: argparse.Namespace,
    ) -> ExtractedSection:
        prs = GithubApi.get_paginated(
            f"/repos/{repo}/pulls?state=open&sort=updated&direction=desc",
            max_pages=2,
        )
        prs = apply_user_filter(prs, args, prefix="active")
        pr_rows: List[Dict[str, Any]] = []
        for p in prs[:_MAX_PRS_PER_REPO]:
            pr_rows.append(
                {
                    "number": p.get("number"),
                    "title": (p.get("title") or "")[:80],
                    "user": (p.get("user") or {}).get("login"),
                    "draft": p.get("draft"),
                    "head": (p.get("head") or {}).get("ref"),
                    "base": (p.get("base") or {}).get("ref"),
                    "review_comments": p.get("review_comments"),
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
