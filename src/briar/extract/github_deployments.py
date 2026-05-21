"""Deployments / environments / CI status extractor.

Provider-agnostic: reads via a `RepositoryProvider`. Named
`github-deployments` for back-compat with existing runbook YAMLs, but
the logic works against any provider that overrides
``list_environments`` / ``list_deployments`` / ``list_ci_runs``.
Bitbucket Cloud provider returns empty lists today; an override in
``_providers/bitbucket.py`` will fill them in."""

from __future__ import annotations

import argparse
from typing import List

from briar.extract.base import ExtractedSection, RepoBackedExtractor


class ExtractGithubDeployments(RepoBackedExtractor):
    name = "github-deployments"
    description = "environments, deployments, recent CI runs"
    requires_github = True  # legacy flag

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--deploy-repo",
            action="append",
            default=[],
            help="Repository slug to scan for deployments. Repeatable.",
        )

    def is_available(self, args: argparse.Namespace) -> bool:
        if not args.deploy_repo:
            return False
        try:
            provider = self._provider(args)
        except Exception:  # noqa: BLE001
            return False
        return provider.is_available()

    def extract(self, args: argparse.Namespace) -> ExtractedSection:
        provider = self._provider(args)
        subsections = [self._scan_repo(repo, provider) for repo in args.deploy_repo]
        return ExtractedSection(
            title=f"GitHub deployments — {len(subsections)} repo(s)",
            body="Environments, recent deployments, latest CI runs.",
            subsections=subsections,
        )

    def _scan_repo(self, repo: str, provider) -> ExtractedSection:
        environments = provider.list_environments(repo)
        env_rows = [
            {
                "name": e.name,
                "protection_rules": e.protection_rule_count,
                "url": e.url,
            }
            for e in environments
        ]

        deployments = provider.list_deployments(repo, limit=10)
        recent_deploys = [
            {
                "id": d.id,
                "env": d.environment,
                "sha": d.sha,
                "creator": d.creator,
                "created_at": d.created_at,
            }
            for d in deployments
        ]

        runs = provider.list_ci_runs(repo, limit=5)
        ci_rows = [
            {
                "name": r.name,
                "status": r.status,
                "conclusion": r.conclusion,
                "head_branch": r.head_branch,
                "created_at": r.created_at,
            }
            for r in runs
        ]

        body_parts: List[str] = []
        if env_rows:
            body_parts.append("**Environments:**")
            for r in env_rows:
                body_parts.append(f"- {r['name']}  protection_rules={r['protection_rules']}")
        if recent_deploys:
            body_parts.append("\n**Recent deployments:**")
            for d in recent_deploys:
                body_parts.append(f"- {d['env']}  sha={d['sha']}  by={d['creator']}  " f"at={d['created_at']}")
        if ci_rows:
            body_parts.append("\n**Recent CI runs:**")
            for c in ci_rows:
                body_parts.append(f"- {c['name']}  status={c['status']}  " f"conclusion={c['conclusion']}  branch={c['head_branch']}")
        return ExtractedSection(
            title=repo,
            body="\n".join(body_parts) if body_parts else "_no deployments_",
            data={
                "environments": env_rows,
                "recent_deployments": recent_deploys,
                "recent_ci_runs": ci_rows,
            },
        )
