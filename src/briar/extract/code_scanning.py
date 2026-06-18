"""Open static-analysis findings grouped by rule and file.

Surfaces a repo's open code-scanning alerts (GitHub CodeQL, Bitbucket
Code Insights, …) so an agent knows which rules are firing and where
before it touches the code. Findings are grouped by `rule_id`: how many
times each rule fires, its severity, and one example file so the agent
can jump straight to a representative offender.

Provider-agnostic: talks to a `RepositoryProvider` via
``list_code_scanning_alerts``, not to GitHub directly. A provider
without a code-scanning API renders this section empty (the verb's
empty default), same graceful degradation as the other code-quality
extractors."""

from __future__ import annotations

import argparse
from collections import Counter
from typing import Any, Dict, List

from briar.extract._provider import ScanAlert
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section


class ExtractCodeScanning(RepoBackedExtractor):
    name = "code-scanning"
    heading = "Code scanning alerts"
    description = "open static-analysis findings grouped by rule and file"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--scan-repo",
            action="append",
            default=[],
            help="Repository slug to scan (e.g. owner/repo). Repeatable.",
        )
        parser.add_argument(
            "--scan-max",
            type=int,
            default=200,
            help="Max code-scanning alerts to fetch per repo (default: 200)",
        )
        parser.add_argument(
            "--scan-top-n",
            type=int,
            default=10,
            help="How many rules to surface per repo (default: 10)",
        )

    _availability_arg = "scan_repo"

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.scan_repo:
            section = self._scan_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"Code scanning alerts — {len(per_repo)} repo(s)",
            body=(
                "Open static-analysis findings grouped by rule. When you "
                "touch a flagged file, fix the finding rather than working "
                "around it; the example file points at a representative "
                "offender for each rule."
            ),
            subsections=per_repo,
        )

    def _scan_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        alerts: List[ScanAlert] = provider.list_code_scanning_alerts(repo, max_count=args.scan_max)
        alerts = [a for a in alerts if a.state == "open"]
        if not alerts:
            return empty_section()

        by_severity: Counter = Counter(a.severity for a in alerts)

        rule_counts: Counter = Counter()
        rule_example: Dict[str, str] = {}
        rule_severity: Dict[str, str] = {}
        for a in alerts:
            rule_counts[a.rule_id] += 1
            if a.rule_id not in rule_example:
                rule_example[a.rule_id] = a.file_path
                rule_severity[a.rule_id] = a.severity

        top_rules: List[Dict[str, Any]] = [
            {
                "rule_id": rule_id,
                "count": count,
                "severity": rule_severity[rule_id],
                "example_file": rule_example[rule_id],
            }
            for rule_id, count in rule_counts.most_common(args.scan_top_n)
        ]

        data: Dict[str, Any] = {
            "repo": repo,
            "open_alert_count": len(alerts),
            "by_severity": dict(by_severity),
            "top_rules": top_rules,
        }
        body_lines = [f"{len(alerts)} open alert(s) across {len(rule_counts)} rule(s)."]
        for rule in top_rules:
            body_lines.append(f"- `{rule['rule_id']}` ×{rule['count']} " f"({rule['severity']}) e.g. {rule['example_file']}")
        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data=data,
        )
