"""GitHub deployments / environments / CI status extractor."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List, Optional

from briar.extract._gh import GithubApi
from briar.extract.base import ExtractedSection, KnowledgeExtractor


class ExtractGithubDeployments(KnowledgeExtractor):
    name = "github-deployments"
    description = "environments, deployments, recent CI runs"
    requires_github = True

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--deploy-repo", action="append", default=[],
            help="GitHub repo to scan for deployments. Repeatable.",
        )

    def is_available(self, args: argparse.Namespace) -> bool:
        return bool(args.deploy_repo) and bool(GithubApi.auth_token())

    def extract(self, args: argparse.Namespace) -> Optional[ExtractedSection]:
        subsections = [self._scan_repo(repo) for repo in args.deploy_repo]
        return ExtractedSection(
            title=f"GitHub deployments — {len(subsections)} repo(s)",
            body="Environments, recent deployments, latest CI runs.",
            subsections=subsections,
        )

    def _scan_repo(self, repo: str) -> ExtractedSection:
        env_envelope = GithubApi.get_json(f"/repos/{repo}/environments")
        envs = (
            env_envelope.get("environments", [])
            if type(env_envelope) is dict else []
        )
        env_rows: List[Dict[str, Any]] = [
            {
                "name": e.get("name"),
                "protection_rules": len(e.get("protection_rules") or []),
                "url": e.get("html_url"),
            }
            for e in envs
        ]

        deployments = GithubApi.get_paginated(
            f"/repos/{repo}/deployments", max_pages=1, per_page=20,
        )
        recent_deploys = [
            {
                "id": d.get("id"),
                "env": d.get("environment"),
                "sha": (d.get("sha") or "")[:7],
                "creator": (d.get("creator") or {}).get("login"),
                "created_at": d.get("created_at"),
            }
            for d in deployments[:10]
        ]

        runs_envelope = GithubApi.get_json(f"/repos/{repo}/actions/runs?per_page=10")
        runs = (
            runs_envelope.get("workflow_runs", [])
            if type(runs_envelope) is dict else []
        )
        ci_rows = [
            {
                "name": r.get("name"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "head_branch": r.get("head_branch"),
                "created_at": r.get("created_at"),
            }
            for r in runs[:5]
        ]

        body_parts: List[str] = []
        if env_rows:
            body_parts.append("**Environments:**")
            for r in env_rows:
                body_parts.append(
                    f"- {r['name']}  protection_rules={r['protection_rules']}"
                )
        if recent_deploys:
            body_parts.append("\n**Recent deployments:**")
            for d in recent_deploys:
                body_parts.append(
                    f"- {d['env']}  sha={d['sha']}  by={d['creator']}  "
                    f"at={d['created_at']}"
                )
        if ci_rows:
            body_parts.append("\n**Recent CI runs:**")
            for c in ci_rows:
                body_parts.append(
                    f"- {c['name']}  status={c['status']}  "
                    f"conclusion={c['conclusion']}  branch={c['head_branch']}"
                )
        return ExtractedSection(
            title=repo,
            body="\n".join(body_parts) if body_parts else "_no deployments_",
            data={
                "environments": env_rows,
                "recent_deployments": recent_deploys,
                "recent_ci_runs": ci_rows,
            },
        )
