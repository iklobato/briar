"""GitHub source template.

Family: `tracker`. Reads issues from `<owner>/<repo>`. Brings action
tools for commenting on issues, opening PRs, and committing files.

User filters (`--github-authors-allow`, `--github-authors-block`,
`--github-assignees-allow`, `--github-assignees-block`) restrict which
issues the agent sees. Filters compose: allow ∩ ¬block."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.iac.scaffold.sources.base import SourceTemplate


class SourceGithub(SourceTemplate):
    kind = "github"
    family = "tracker"
    default_provider_for_oauth = "github"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        # Identity flags for the GitHub source. Registered here (not on
        # the scaffold templates) so a Bitbucket-only or AWS-only scaffold
        # doesn't have GitHub-shaped required flags. Validation that they
        # are set happens in `build_source` when `--source github` is
        # actually selected.
        parser.add_argument(
            "--owner",
            help="GitHub org/user (required when --source includes github)",
        )
        parser.add_argument(
            "--repo",
            help="GitHub repo name (required when --source includes github)",
        )
        parser.add_argument(
            "--github-authors-allow",
            action="append",
            default=[],
            help="only include issues whose creator is in this list (repeatable)",
        )
        parser.add_argument(
            "--github-authors-block",
            action="append",
            default=[],
            help="exclude issues whose creator is in this list (repeatable)",
        )
        parser.add_argument(
            "--github-assignees-allow",
            action="append",
            default=[],
            help="only include issues with an assignee in this list (repeatable)",
        )
        parser.add_argument(
            "--github-assignees-block",
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
        owner = ns.get("owner")
        repo = ns.get("repo")
        if not owner or not repo:
            raise SystemExit("--source github requires --owner AND --repo")
        config: Dict[str, Any] = {
            "owner": owner,
            "repo": f"{owner}/{repo}",
            "include": "open",
        }
        for key, values in self._user_filters(args).items():
            if values:
                config[key] = values

        return {
            "key": f"{key_prefix}-gh-issues",
            "name": f"{key_prefix}-gh-issues",
            "kind": "github",
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
                "key": f"{key_prefix}-gh-comment",
                "name": f"{key_prefix}-gh-comment",
                "description": "Post a comment on a GitHub issue/PR",
                "implementation_ref": "github.comment_on_issue",
                "side_effect": "mutate",
                **auth,
            },
            {
                "key": f"{key_prefix}-gh-open-pr",
                "name": f"{key_prefix}-gh-open-pr",
                "description": "Open a draft pull request",
                "implementation_ref": "github.open_pr",
                "side_effect": "mutate",
                **auth,
            },
            {
                "key": f"{key_prefix}-gh-commit",
                "name": f"{key_prefix}-gh-commit",
                "description": "Commit files to a branch",
                "implementation_ref": "github.commit_files",
                "side_effect": "mutate",
                **auth,
            },
        ]

    def target(self, args: argparse.Namespace) -> str:
        ns = vars(args)
        owner = ns.get("owner") or ""
        repo = ns.get("repo") or ""
        if not owner or not repo:
            return ""
        return f"{owner}/{repo}"

    @staticmethod
    def _auth(args: argparse.Namespace) -> Dict[str, Any]:
        ns = vars(args)
        mode = ns.get("auth_mode") or "oauth"
        if mode == "pat":
            secret_id = ns.get("github_secret_id")
            if not secret_id:
                raise SystemExit("--source github with --auth-mode pat requires " "--github-secret-id <secret-uuid>")
            return {"credentials_ref": secret_id, "credential_binding": None}
        return {
            "credentials_ref": None,
            "credential_binding": {
                "kind": "oauth_connection",
                "provider": "github",
            },
        }

    @staticmethod
    def _user_filters(args: argparse.Namespace) -> Dict[str, List[str]]:
        ns = vars(args)
        return {
            "authors_allow": list(ns.get("github_authors_allow") or []),
            "authors_block": list(ns.get("github_authors_block") or []),
            "assignees_allow": list(ns.get("github_assignees_allow") or []),
            "assignees_block": list(ns.get("github_assignees_block") or []),
        }
