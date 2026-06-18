"""Score commit-message hygiene across one or more repos.

Reads recent commit subjects and reports how well they follow the
Conventional Commits prefix grammar plus basic subject-line length
hygiene (too-long subjects wrap badly in `git log --oneline`; too-short
ones carry no information). Output points an agent at the convention it
should match when it writes its own commits.

Provider-agnostic: talks to a `RepositoryProvider`, not GitHub
directly. Only `Commit.message` (the subject line) is inspected, so a
provider that returns commits without file lists is still fully usable
here."""

from __future__ import annotations

import argparse
import re
from statistics import median
from typing import Any, Dict, List

from briar.extract._provider import Commit
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section

_CONVENTIONAL_RE = re.compile(
    r"^(feat|fix|chore|docs|refactor|test|style|perf|build|ci|revert)(\([^)]+\))?!?: .+",
    re.I,
)


class ExtractCommitMessageQuality(RepoBackedExtractor):
    name = "commit-message-quality"
    heading = "Commit message quality"
    description = "conventional-commits adherence and subject-line hygiene"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--msg-repo",
            action="append",
            default=[],
            help="Repository slug to analyse. Repeatable.",
        )
        parser.add_argument(
            "--msg-since-days",
            type=int,
            default=90,
            help="Commit lookback window in days (default: 90)",
        )
        parser.add_argument(
            "--msg-max-commits",
            type=int,
            default=200,
            help="Max commits to inspect per repo (default: 200)",
        )

    _availability_arg = "msg_repo"

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.msg_repo:
            section = self._analyse_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"Commit message quality — {len(per_repo)} repo(s)",
            body=(
                "How well recent commit subjects follow Conventional "
                "Commits and basic length hygiene. Agents should match the "
                "established convention when writing their own commits."
            ),
            subsections=per_repo,
        )

    def _analyse_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        commits: List[Commit] = provider.list_recent_commits(
            repo,
            since_days=args.msg_since_days,
            max_count=args.msg_max_commits,
        )
        if not commits:
            return empty_section()

        total = len(commits)
        conventional_count = 0
        too_long_count = 0
        too_short_count = 0
        lengths: List[int] = []
        for commit in commits:
            subject = commit.message  # already the first line / subject
            length = len(subject)
            lengths.append(length)
            if _CONVENTIONAL_RE.match(subject):
                conventional_count += 1
            if length > 72:
                too_long_count += 1
            if length < 10:
                too_short_count += 1

        data: Dict[str, Any] = {
            "repo": repo,
            "commit_sample_size": total,
            "conventional_rate": round(conventional_count / total, 2),
            "long_subject_rate": round(too_long_count / total, 2),
            "short_subject_rate": round(too_short_count / total, 2),
            "median_subject_length": round(median(lengths)),
        }
        body_lines = [
            f"- commit sample: **{total}**",
            f"- conventional-commits rate: **{data['conventional_rate']}**",
            f"- long subjects (>72 chars): **{data['long_subject_rate']}**",
            f"- short subjects (<10 chars): **{data['short_subject_rate']}**",
            f"- median subject length: **{data['median_subject_length']}**",
        ]
        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data=data,
        )
