"""Jira source template.

Family: `tracker`. Reads issues from one or more Jira projects.
Brings action tools: `jira.comment`, `jira.transition`, `jira.update_issue`.

Auth: Atlassian OAuth connection by default, or a stored Atlassian PAT
via `--jira-secret-id`."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.iac.scaffold.sources.base import SourceTemplate


class SourceJira(SourceTemplate):
    kind = "jira"
    default_provider_for_oauth = "atlassian"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--jira-project",
            action="append",
            default=[],
            help="Jira project key to include (repeatable; defaults to all)",
        )
        parser.add_argument(
            "--jira-jql",
            help="Optional JQL filter applied on top of the project list",
        )
        parser.add_argument(
            "--jira-secret-id",
            help="Secret UUID holding an Atlassian PAT (skip OAuth)",
        )
        parser.add_argument(
            "--jira-authors-allow",
            action="append",
            default=[],
            help="reporter allowlist (repeatable; folds into JQL)",
        )
        parser.add_argument(
            "--jira-authors-block",
            action="append",
            default=[],
            help="reporter blocklist (repeatable; folds into JQL)",
        )
        parser.add_argument(
            "--jira-assignees-allow",
            action="append",
            default=[],
            help="assignee allowlist (repeatable; folds into JQL)",
        )
        parser.add_argument(
            "--jira-assignees-block",
            action="append",
            default=[],
            help="assignee blocklist (repeatable; folds into JQL)",
        )

    def build_source(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> Dict[str, Any]:
        ns = vars(args)
        config: Dict[str, Any] = {"include": "open"}
        projects = ns.get("jira_project") or []
        if projects:
            config["projects"] = projects
        jql = ns.get("jira_jql")
        if jql:
            config["jql"] = jql
        # Per-source --jira-authors-allow wins; otherwise the shared
        # --authors-allow / --assignees-* flags apply (base._user_filters).
        for field, values in self._user_filters(args).items():
            if values:
                config[field] = values

        return {
            "key": f"{key_prefix}-jira",
            "name": f"{key_prefix}-jira",
            "kind": "jira",
            "config": config,
            **self._auth(args),
        }

    def build_tools(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> List[Dict[str, Any]]:
        auth = self._auth(args)
        return [
            {
                "key": f"{key_prefix}-jira-comment",
                "name": f"{key_prefix}-jira-comment",
                "description": "Add a comment to a Jira issue",
                "implementation_ref": "jira.comment",
                "side_effect": "mutate",
                **auth,
            },
            {
                "key": f"{key_prefix}-jira-transition",
                "name": f"{key_prefix}-jira-transition",
                "description": "Transition a Jira issue to a new status",
                "implementation_ref": "jira.transition",
                "side_effect": "mutate",
                **auth,
            },
            {
                "key": f"{key_prefix}-jira-update",
                "name": f"{key_prefix}-jira-update",
                "description": "Update fields on a Jira issue",
                "implementation_ref": "jira.update_issue",
                "side_effect": "mutate",
                **auth,
            },
        ]

    def target(self, args: argparse.Namespace) -> str:
        ns = vars(args)
        projects = ns.get("jira_project") or []
        if not projects:
            return ""
        return projects[0]

    @staticmethod
    def _auth(args: argparse.Namespace) -> Dict[str, Any]:
        ns = vars(args)
        secret_id = ns.get("jira_secret_id")
        if secret_id:
            return {"credentials_ref": secret_id, "credential_binding": None}
        return {
            "credentials_ref": None,
            "credential_binding": {
                "kind": "oauth_connection",
                "provider": "atlassian",
            },
        }
