"""Surface open dependency vulnerabilities per repo, by severity.

Talks to the `RepositoryProvider.list_dependabot_alerts` verb (GitHub
Dependabot today; any provider that implements the verb tomorrow) and
ranks the open alerts so an agent touching dependencies knows which
packages are on fire and which manifests declare them. Defaults are
graceful: a provider without a security-scanning API renders an empty
section instead of erroring."""

from __future__ import annotations

import argparse
from collections import Counter
from typing import Any, Dict, List

from briar.extract._provider import SecurityAlert
from briar.extract.base import ExtractedSection, RepoBackedExtractor, empty_section

# Severity rank — higher sorts first. Also the canonical key set for the
# stable `by_severity` breakdown (all four always present, 0-default).
_SEVERITY_RANK: Dict[str, int] = {"critical": 3, "high": 2, "medium": 1, "low": 0}


class ExtractDependencyHealth(RepoBackedExtractor):
    name = "dependency-health"
    heading = "Dependency health"
    description = "open dependency vulnerabilities by severity"
    requires_github = True  # legacy flag

    _availability_arg = "deps_repo"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--deps-repo",
            action="append",
            default=[],
            help="Repository slug to scan for dependency alerts. Repeatable.",
        )
        parser.add_argument(
            "--deps-max",
            type=int,
            default=200,
            help="Max dependency alerts to inspect per repo (default: 200)",
        )

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        per_repo: List[ExtractedSection] = []
        for repo in args.deps_repo:
            section = self._scan_repo(repo, args, provider)
            if not section.is_empty:
                per_repo.append(section)
        if not per_repo:
            return empty_section()
        return ExtractedSection(
            title=f"Dependency health — {len(per_repo)} repo(s)",
            body=(
                "Open dependency vulnerabilities by severity. Agents adding "
                "or bumping dependencies should prefer versions that clear "
                "these alerts and avoid reintroducing the listed packages."
            ),
            subsections=per_repo,
        )

    def _scan_repo(self, repo: str, args: argparse.Namespace, provider) -> ExtractedSection:
        alerts: List[SecurityAlert] = provider.list_dependabot_alerts(repo, max_count=args.deps_max)
        # Defensive: the provider should only return open alerts, but a
        # vendor that mixes in fixed/dismissed ones must not inflate counts.
        alerts = [a for a in alerts if a.state == "open"]
        if not alerts:
            return empty_section()

        by_severity: Counter = Counter()
        for alert in alerts:
            by_severity[alert.severity] += 1
        # Stable shape: all four keys present, 0-default.
        severity_counts = {sev: by_severity.get(sev, 0) for sev in _SEVERITY_RANK}

        ranked = sorted(
            alerts,
            key=lambda a: (-_SEVERITY_RANK.get(a.severity, -1), a.package),
        )
        top_alerts = [
            {
                "package": a.package,
                "severity": a.severity,
                "summary": a.summary,
                "manifest": a.manifest,
            }
            for a in ranked[:10]
        ]

        data: Dict[str, Any] = {
            "repo": repo,
            "open_alert_count": len(alerts),
            "by_severity": severity_counts,
            "top_alerts": top_alerts,
        }

        breakdown = ", ".join(f"{severity_counts[sev]} {sev}" for sev in _SEVERITY_RANK)
        body_lines = [f"- {len(alerts)} open ({breakdown})"]
        for a in top_alerts:
            body_lines.append(f"  - `{a['package']}` ({a['severity']}): {a['summary']}")
        return ExtractedSection(
            title=repo,
            body="\n".join(body_lines),
            data=data,
        )
