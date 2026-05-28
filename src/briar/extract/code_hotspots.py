"""File co-change clustering — surfaces which files change together.

Builds a small co-occurrence matrix from recent commits: for any
given source file, which files most often appear in the same commit?
That signal points an agent at the files it should ALSO touch when
editing a related one (test files for source files, migrations for
model changes, fixture files for templates, …).

Each repo's section lists the top hotspot files (by total commit
involvement) + their top 3 co-changing partners. Heavy on commit
fetches — use a wide `--hotspots-since-days` only if you have the
API quota."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from typing import Any, Dict, List

from briar.extract._provider import Commit
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section


class ExtractCodeHotspots(RepoBackedExtractor):
    name = "code-hotspots"
    heading = "Code hotspots"
    description = "files that change together — co-change clustering for context"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--hotspots-repo",
            action="append",
            default=[],
            help="Repository slug to analyse. Repeatable.",
        )
        parser.add_argument(
            "--hotspots-since-days",
            type=int,
            default=30,
            help="Commit lookback window in days (default: 30)",
        )
        parser.add_argument(
            "--hotspots-max-commits",
            type=int,
            default=100,
            help="Max commits to inspect per repo (default: 100)",
        )
        parser.add_argument(
            "--hotspots-top-n",
            type=int,
            default=10,
            help="How many hotspot files to surface per repo (default: 10)",
        )

    def is_available(self, args: argparse.Namespace) -> bool:
        if not args.hotspots_repo:
            return False
        try:
            provider = self._provider(args)
        except Exception:  # noqa: BLE001
            return False
        return provider.is_available()

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.hotspots_repo:
            section = self._analyse_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"Code hotspots — {len(per_repo)} repo(s)",
            body=(
                "Files that frequently change in the same commit. When you "
                "touch one, consider whether you should also touch its "
                "co-changers (tests, migrations, fixtures, related modules)."
            ),
            subsections=per_repo,
        )

    def _analyse_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        commits: List[Commit] = provider.list_recent_commits(
            repo,
            since_days=args.hotspots_since_days,
            max_count=args.hotspots_max_commits,
        )
        # The provider may return commits without file lists when the
        # endpoint cost-budget caps the per-commit fetch — filter to
        # only those with file lists so the co-change matrix is real.
        commits = [c for c in commits if c.file_paths]
        if not commits:
            return empty_section()

        file_touch_count: Counter = Counter()
        co_occurrence: Dict[str, Counter] = defaultdict(Counter)

        for commit in commits:
            files = list(set(commit.file_paths))  # de-dup within a single commit
            for f in files:
                file_touch_count[f] += 1
            # Co-occurrence: every pair of files that appeared in this commit.
            for i, f_a in enumerate(files):
                for f_b in files[i + 1 :]:
                    co_occurrence[f_a][f_b] += 1
                    co_occurrence[f_b][f_a] += 1

        top_hotspots = file_touch_count.most_common(args.hotspots_top_n)

        body_parts: List[str] = [
            f"Sample: {len(commits)} commits over {args.hotspots_since_days} days",
            "",
        ]
        data_rows: List[Dict[str, Any]] = []
        for path, touches in top_hotspots:
            co_changers = [pair for pair in co_occurrence[path].most_common(3) if pair[1] > 1]
            body_parts.append(f"- `{path}` (touched {touches}×)")
            if co_changers:
                pretty = ", ".join(f"`{p}` ({n})" for p, n in co_changers)
                body_parts.append(f"  - co-changes with: {pretty}")
            data_rows.append(
                {
                    "path": path,
                    "touches": touches,
                    "top_co_changers": [{"path": p, "count": n} for p, n in co_changers],
                }
            )
        return ExtractedSection(
            title=repo,
            body="\n".join(body_parts),
            data={"hotspots": data_rows, "commit_sample_size": len(commits)},
        )
