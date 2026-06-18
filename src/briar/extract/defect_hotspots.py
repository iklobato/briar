"""Defect-prone file ranking — churn × bug-fixes × size.

Surfaces the files most likely to break next, so an agent editing them
treads carefully (extra tests, smaller diffs, closer review). The signal
combines three classic defect predictors:

  * churn        — how often the file is touched (commit involvement)
  * bug-fixes    — how many of those touches were bug-fix commits
  * size         — bigger files concentrate more risk per change

A file that changes constantly AND keeps getting bug-fix commits AND is
large scores highest. Heavy on commit fetches plus a capped batch of
`read_file` calls (top-N only) — use a wide `--risk-since-days` only if
you have the API quota."""

from __future__ import annotations

import argparse
import math
import re
from collections import Counter
from typing import Any, Dict, List

from briar.extract._provider import Commit
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section

# A commit counts as a bug-fix when its message mentions one of these
# verbs/nouns as a whole word — the standard SZZ-style heuristic.
_BUGFIX_RE = re.compile(r"\b(fix|fixes|fixed|bug|hotfix|patch|revert|regression)\b")


class ExtractDefectHotspots(RepoBackedExtractor):
    name = "defect-hotspots"
    heading = "Defect hotspots"
    description = "files most likely to break — churn × bug-fixes × size risk score"
    requires_github = True  # legacy flag

    _availability_arg = "risk_repo"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--risk-repo",
            action="append",
            default=[],
            help="Repository slug to analyse. Repeatable.",
        )
        parser.add_argument(
            "--risk-since-days",
            type=int,
            default=90,
            help="Commit lookback window in days (default: 90)",
        )
        parser.add_argument(
            "--risk-max-commits",
            type=int,
            default=200,
            help="Max commits to inspect per repo (default: 200)",
        )
        parser.add_argument(
            "--risk-top-n",
            type=int,
            default=10,
            help="How many risky files to surface per repo (default: 10)",
        )

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.risk_repo:
            section = self._analyse_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"Defect hotspots — {len(per_repo)} repo(s)",
            body=(
                "Files ranked by defect risk = churn × (1 + bug-fixes) × "
                "log(size). High-scoring files break most often when "
                "touched — diff them small, test them hard, review closely."
            ),
            subsections=per_repo,
        )

    def _analyse_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        commits: List[Commit] = provider.list_recent_commits(
            repo,
            since_days=args.risk_since_days,
            max_count=args.risk_max_commits,
        )
        # The provider may return commits without file lists when the
        # endpoint cost-budget caps the per-commit fetch — filter to
        # only those with file lists, mirroring code-hotspots.
        commits = [c for c in commits if c.file_paths]
        if not commits:
            return empty_section()

        commits_touched: Counter = Counter()
        bugfix_touched: Counter = Counter()
        for commit in commits:
            is_bugfix = bool(_BUGFIX_RE.search(commit.message.lower()))
            for path in set(commit.file_paths):  # de-dup within a single commit
                commits_touched[path] += 1
                if is_bugfix:
                    bugfix_touched[path] += 1

        # Rank by churn first; read_file LOC only for the top-N candidates
        # so the (expensive) file reads stay capped.
        candidates = [path for path, _ in commits_touched.most_common(args.risk_top_n)]

        rows: List[Dict[str, Any]] = []
        for path in candidates:
            content = provider.read_file(repo, path)
            loc = len(content.splitlines()) if content else 0
            churn = commits_touched[path]
            bugfix = bugfix_touched[path]
            # +2 keeps log() above zero so a 0/1-line file doesn't collapse
            # the whole score to ~0.
            risk_score = round(churn * (1 + bugfix) * math.log(loc + 2), 2)
            rows.append(
                {
                    "path": path,
                    "commits": churn,
                    "bugfix_commits": bugfix,
                    "loc": loc,
                    "risk_score": risk_score,
                }
            )

        rows.sort(key=lambda r: r["risk_score"], reverse=True)

        body_lines = [f"- `{r['path']}` — risk **{r['risk_score']}** " f"(churn {r['commits']}, bug-fixes {r['bugfix_commits']}, {r['loc']} loc)" for r in rows]
        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data={
                "repo": repo,
                "commit_sample_size": len(commits),
                "top_risky": rows,
            },
        )
