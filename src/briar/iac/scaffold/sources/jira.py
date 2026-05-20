"""Jira source template.

Family: `tracker`. Reads issues from one or more Jira projects.
Brings action tools: `jira.comment`, `jira.transition`, `jira.update_issue`.

Auth: Atlassian OAuth connection by default, or a stored Atlassian PAT
via `--jira-secret-id`. The two flags mirror the github family's
`--auth-mode` / `--github-secret-id` semantics."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.iac.scaffold.sources.base import SourceTemplate


def _jira_auth(args: argparse.Namespace) -> Dict[str, Any]:
    secret_id = getattr(args, "jira_secret_id", None)
    if secret_id:
        return {"credentials_ref": secret_id, "credential_binding": None}
    # Default: OAuth connection to atlassian.
    return {
        "credentials_ref": None,
        "credential_binding": {
            "kind": "oauth_connection", "provider": "atlassian",
        },
    }


class SourceJira(SourceTemplate):
    kind = "jira"
    family = "tracker"
    default_provider_for_oauth = "atlassian"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--jira-project", action="append", default=[],
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
        # User-filter flags (mirror the GitHub side). Identifiers are
        # whatever your Jira tenant accepts — accountId or email.
        parser.add_argument(
            "--jira-authors-allow", action="append", default=[],
            help="reporter allowlist (repeatable; folds into JQL)",
        )
        parser.add_argument(
            "--jira-authors-block", action="append", default=[],
            help="reporter blocklist (repeatable; folds into JQL)",
        )
        parser.add_argument(
            "--jira-assignees-allow", action="append", default=[],
            help="assignee allowlist (repeatable; folds into JQL)",
        )
        parser.add_argument(
            "--jira-assignees-block", action="append", default=[],
            help="assignee blocklist (repeatable; folds into JQL)",
        )

    def build_source(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> Dict[str, Any]:
        config: Dict[str, Any] = {"include": "open"}
        projects = getattr(args, "jira_project", []) or []
        if projects:
            config["projects"] = projects
        jql = getattr(args, "jira_jql", None)
        if jql:
            config["jql"] = jql
        # User filters — connector folds these into the JQL it issues.
        for field, attr in (
            ("authors_allow",   "jira_authors_allow"),
            ("authors_block",   "jira_authors_block"),
            ("assignees_allow", "jira_assignees_allow"),
            ("assignees_block", "jira_assignees_block"),
        ):
            values = list(getattr(args, attr, None) or [])
            if values:
                config[field] = values

        return {
            "key": f"{key_prefix}-jira",
            "name": f"{key_prefix}-jira",
            "kind": "jira",
            "config": config,
            **_jira_auth(args),
        }

    def build_tools(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> List[Dict[str, Any]]:
        auth = _jira_auth(args)
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
