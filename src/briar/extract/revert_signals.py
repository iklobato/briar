"""Revert & hotfix signals — fragile areas the test/review net missed.

Scans recent commit subjects for revert commits ("Revert ...") and
emergency-fix language (hotfix, rollback, quick-fix). A commit that
reverts or hotfixes is, by definition, a change the normal review +
CI path let through broken — so the files those commits touch are the
ones an agent should treat with extra care (more tests, tighter
review) when editing.

Each repo's subsection reports the revert rate over the sampled window
plus the files most often implicated in a revert/hotfix commit. Like
code-hotspots this is commit-fetch heavy — keep the window modest
unless you have the API quota."""

from __future__ import annotations

import argparse
import re
from collections import Counter
from typing import Any, Dict, List

from briar.extract._provider import Commit
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section

_REVERT_RE = re.compile(r"^revert\b|\brevert(s|ed|ing)?\b", re.I)
_HOTFIX_RE = re.compile(r"\b(hotfix|hot-fix|emergency|rollback|roll back|quick ?fix)\b", re.I)


class ExtractRevertSignals(RepoBackedExtractor):
    name = "revert-signals"
    heading = "Revert & hotfix signals"
    description = "reverts and emergency fixes — fragile areas the test/review net missed"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--revert-repo",
            action="append",
            default=[],
            help="Repository slug to analyse. Repeatable.",
        )
        parser.add_argument(
            "--revert-since-days",
            type=int,
            default=90,
            help="Commit lookback window in days (default: 90)",
        )
        parser.add_argument(
            "--revert-max-commits",
            type=int,
            default=200,
            help="Max commits to inspect per repo (default: 200)",
        )

    _availability_arg = "revert_repo"

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.revert_repo:
            section = self._analyse_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"Revert & hotfix signals — {len(per_repo)} repo(s)",
            body=(
                "Commits that revert or hotfix earlier work — changes the "
                "normal review + CI path let through broken. The files they "
                "touch are fragile; treat them with extra care (more tests, "
                "tighter review) when editing."
            ),
            subsections=per_repo,
        )

    def _analyse_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        commits: List[Commit] = provider.list_recent_commits(
            repo,
            since_days=args.revert_since_days,
            max_count=args.revert_max_commits,
        )
        if not commits:
            return empty_section()

        revert_commits = [c for c in commits if _REVERT_RE.search(c.message)]
        hotfix_commits = [c for c in commits if _HOTFIX_RE.search(c.message)]
        revert_count = len(revert_commits)
        hotfix_count = len(hotfix_commits)
        revert_rate = round(revert_count / len(commits), 2)

        fragile_counter: Counter = Counter()
        for commit in revert_commits + hotfix_commits:
            for path in set(commit.file_paths):
                fragile_counter[path] += 1
        fragile_files: List[Dict[str, Any]] = [{"path": path, "count": count} for path, count in fragile_counter.most_common(10)]

        data: Dict[str, Any] = {
            "repo": repo,
            "commit_sample_size": len(commits),
            "revert_count": revert_count,
            "revert_rate": revert_rate,
            "hotfix_count": hotfix_count,
            "fragile_files": fragile_files,
        }

        body_lines = [
            f"Sample: {len(commits)} commits — {revert_count} reverts " f"(rate {revert_rate}), {hotfix_count} hotfixes",
        ]
        for f in fragile_files:
            body_lines.append(f"- `{f['path']}` ({f['count']}×)")
        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data=data,
        )
