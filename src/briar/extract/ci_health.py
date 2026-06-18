"""CI health — pass rate, flaky workflows, and run-duration trend.

Walks each repo's recent CI runs (``provider.list_ci_runs``) and
summarises three signals an agent should weigh before proposing a
change:

  * pass rate over the completed-run sample,
  * which workflows look flaky (a workflow+branch that flipped between
    success and failure, or any run that needed a retry attempt), and
  * the median run duration so a slow pipeline is visible up front.

Only *completed* runs feed the stats — an in-flight run has no
conclusion to score. Provider-agnostic: talks to a
`RepositoryProvider`, so a Bitbucket-backed runbook reuses the same
logic once that provider implements ``list_ci_runs``."""

from __future__ import annotations

import argparse
from collections import defaultdict
from statistics import median
from typing import Any, Dict, List

from briar.extract._provider import CiRun
from briar.extract._time_util import UNPARSABLE_HOURS, hours_between
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section


class ExtractCiHealth(RepoBackedExtractor):
    name = "ci-health"
    heading = "CI health"
    description = "pass rate, flaky workflows, and run-duration trend"
    requires_github = True  # legacy flag

    _availability_arg = "cihealth_repo"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--cihealth-repo",
            action="append",
            default=[],
            help="Repository slug to analyse. Repeatable.",
        )
        parser.add_argument(
            "--cihealth-limit",
            type=int,
            default=100,
            help="Max CI runs to inspect per repo (default: 100)",
        )

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.cihealth_repo:
            section = self._analyse_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"CI health — {len(per_repo)} repo(s)",
            body=(
                "Pass rate, flaky workflows, and run-duration trend from "
                "the most recent CI runs. A low pass rate or a flaky "
                "workflow is a signal to expect retries — re-run before "
                "assuming a red build is your change."
            ),
            subsections=per_repo,
        )

    def _analyse_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        runs: List[CiRun] = provider.list_ci_runs(repo, limit=args.cihealth_limit)
        completed = [r for r in runs if r.status == "completed" or r.conclusion]
        if not completed:
            return empty_section()

        successes = sum(1 for r in completed if r.conclusion == "success")
        pass_rate = round(successes / len(completed), 2)

        flaky_workflows = self._flaky_workflows(completed)

        durations: List[float] = []
        for r in completed:
            if not (r.created_at and r.updated_at):
                continue
            minutes = hours_between(r.created_at, r.updated_at) * 60
            if minutes == UNPARSABLE_HOURS * 60 or minutes < 0:
                continue
            durations.append(minutes)
        median_run_minutes = round(median(durations), 1) if durations else None

        data: Dict[str, Any] = {
            "repo": repo,
            "completed_runs": len(completed),
            "pass_rate": pass_rate,
            "flaky_workflow_count": len(flaky_workflows),
            "flaky_workflows": flaky_workflows,
            "median_run_minutes": median_run_minutes,
        }

        body_lines = [
            f"- completed runs: **{data['completed_runs']}**",
            f"- pass rate: **{data['pass_rate']}**",
            f"- flaky workflows: **{data['flaky_workflow_count']}**",
        ]
        if flaky_workflows:
            body_lines.append(f"  - {', '.join(flaky_workflows)}")
        if median_run_minutes is not None:
            body_lines.append(f"- median run duration: **{median_run_minutes}m**")
        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data=data,
        )

    @staticmethod
    def _flaky_workflows(completed: List[CiRun]) -> List[str]:
        """Distinct workflow names that look flaky: a (name, branch)
        group with BOTH a success and a failure conclusion, or any run
        that needed a retry (``run_attempt > 1``)."""
        grouped: Dict[tuple, List[CiRun]] = defaultdict(list)
        for r in completed:
            grouped[(r.name, r.head_branch)].append(r)

        flaky: set = set()
        for (name, _branch), group in grouped.items():
            conclusions = {r.conclusion for r in group}
            mixed = "success" in conclusions and "failure" in conclusions
            retried = any(r.run_attempt > 1 for r in group)
            if mixed or retried:
                flaky.add(name)
        return sorted(flaky)
