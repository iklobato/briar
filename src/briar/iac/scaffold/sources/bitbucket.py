"""Bitbucket source template.

Family: `tracker`. Reads issues + pull requests from a single
``<workspace>/<repo_slug>`` Bitbucket Cloud repository. Brings the same
three action tools as the GitHub source — `bitbucket.comment_on_issue`,
`bitbucket.open_pr`, `bitbucket.commit_files` — so an archetype's
substring `tool_filter` matches on `commit`, `comment_on_issue`, and
`open_pr` works unchanged.

Auth: stored Bitbucket app-password (basic auth: username + app
password) by default, or an OAuth connection via
``--auth-mode oauth`` (the downstream runtime resolves the binding to
the operator's Atlassian/Bitbucket OAuth grant).

User filters (`--bitbucket-authors-allow` / `-block`,
`--bitbucket-assignees-allow` / `-block`) restrict which issues the
agent sees. Filters compose: ``allow ∩ ¬block``."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.iac.scaffold.sources.base import SourceTemplate


class SourceBitbucket(SourceTemplate):
    kind = "bitbucket"
    family = "tracker"
    default_provider_for_oauth = "bitbucket"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--bitbucket-workspace",
            help="Bitbucket workspace slug (the part before `/` in a repo URL)",
        )
        parser.add_argument(
            "--bitbucket-repo",
            help="Bitbucket repository slug (the part after `/`)",
        )
        parser.add_argument(
            "--bitbucket-secret-id",
            help="Secret UUID holding a Bitbucket app-password " "(username + app_password stored together); required with --auth-mode pat",
        )
        parser.add_argument(
            "--bitbucket-authors-allow",
            action="append",
            default=[],
            help="only include issues whose reporter is in this list (repeatable)",
        )
        parser.add_argument(
            "--bitbucket-authors-block",
            action="append",
            default=[],
            help="exclude issues whose reporter is in this list (repeatable)",
        )
        parser.add_argument(
            "--bitbucket-assignees-allow",
            action="append",
            default=[],
            help="only include issues with an assignee in this list (repeatable)",
        )
        parser.add_argument(
            "--bitbucket-assignees-block",
            action="append",
            default=[],
            help="exclude issues with an assignee in this list (repeatable)",
        )

    def build_source(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> Dict[str, Any]:
        ns = vars(args)
        workspace = ns.get("bitbucket_workspace")
        repo = ns.get("bitbucket_repo")
        if not workspace or not repo:
            raise SystemExit("--source bitbucket requires --bitbucket-workspace AND --bitbucket-repo")

        config: Dict[str, Any] = {
            "workspace": workspace,
            "repo": f"{workspace}/{repo}",
            "include": "open",
        }
        for key, values in self._user_filters(args).items():
            if values:
                config[key] = values

        return {
            "key": f"{key_prefix}-bb-issues",
            "name": f"{key_prefix}-bb-issues",
            "kind": "bitbucket",
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
                "key": f"{key_prefix}-bb-comment",
                "name": f"{key_prefix}-bb-comment",
                "description": "Post a comment on a Bitbucket issue/PR",
                "implementation_ref": "bitbucket.comment_on_issue",
                "side_effect": "mutate",
                **auth,
            },
            {
                "key": f"{key_prefix}-bb-open-pr",
                "name": f"{key_prefix}-bb-open-pr",
                "description": "Open a draft pull request",
                "implementation_ref": "bitbucket.open_pr",
                "side_effect": "mutate",
                **auth,
            },
            {
                "key": f"{key_prefix}-bb-commit",
                "name": f"{key_prefix}-bb-commit",
                "description": "Commit files to a branch",
                "implementation_ref": "bitbucket.commit_files",
                "side_effect": "mutate",
                **auth,
            },
        ]

    def target(self, args: argparse.Namespace) -> str:
        ns = vars(args)
        workspace = ns.get("bitbucket_workspace") or ""
        repo = ns.get("bitbucket_repo") or ""
        if not workspace or not repo:
            return ""
        return f"{workspace}/{repo}"

    @staticmethod
    def _auth(args: argparse.Namespace) -> Dict[str, Any]:
        ns = vars(args)
        mode = ns.get("auth_mode") or "oauth"
        if mode == "pat":
            secret_id = ns.get("bitbucket_secret_id")
            if not secret_id:
                raise SystemExit("--source bitbucket with --auth-mode pat requires " "--bitbucket-secret-id <secret-uuid>")
            return {"credentials_ref": secret_id, "credential_binding": None}
        return {
            "credentials_ref": None,
            "credential_binding": {
                "kind": "oauth_connection",
                "provider": "bitbucket",
            },
        }

    @staticmethod
    def _user_filters(args: argparse.Namespace) -> Dict[str, List[str]]:
        ns = vars(args)
        return {
            "authors_allow": list(ns.get("bitbucket_authors_allow") or []),
            "authors_block": list(ns.get("bitbucket_authors_block") or []),
            "assignees_allow": list(ns.get("bitbucket_assignees_allow") or []),
            "assignees_block": list(ns.get("bitbucket_assignees_block") or []),
        }
