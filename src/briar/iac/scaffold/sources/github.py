"""GitHub source template.

Family: `tracker`. Reads issues from `<owner>/<repo>`. Brings action
tools for commenting on issues, opening PRs, and committing files.

Authentication mode is controlled by the scaffold's top-level
``--auth-mode`` flag (oauth | pat); this template just consumes that
decision.

User filters (`--github-authors-allow`, `--github-authors-block`,
`--github-assignees-allow`, `--github-assignees-block`) restrict which
issues the agent sees. Filters compose: allow ∩ ¬block. The backend
connector honours all four fields on `source.config`."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.iac.scaffold.sources.base import SourceTemplate


def _gh_auth(args: argparse.Namespace) -> Dict[str, Any]:
    mode = getattr(args, "auth_mode", "oauth")
    if mode == "pat":
        secret_id = getattr(args, "github_secret_id", None)
        if not secret_id:
            raise SystemExit(
                "--source github with --auth-mode pat requires "
                "--github-secret-id <secret-uuid>"
            )
        return {"credentials_ref": secret_id, "credential_binding": None}
    return {
        "credentials_ref": None,
        "credential_binding": {
            "kind": "oauth_connection", "provider": "github",
        },
    }


def _user_filters(args: argparse.Namespace) -> Dict[str, List[str]]:
    """Pick the four optional user-filter fields off the parsed Namespace.
    Empty lists are returned as-is so the connector's `or [] or []`
    pattern works."""
    return {
        "authors_allow":   list(getattr(args, "github_authors_allow", None) or []),
        "authors_block":   list(getattr(args, "github_authors_block", None) or []),
        "assignees_allow": list(getattr(args, "github_assignees_allow", None) or []),
        "assignees_block": list(getattr(args, "github_assignees_block", None) or []),
    }


class SourceGithub(SourceTemplate):
    kind = "github"
    family = "tracker"
    default_provider_for_oauth = "github"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        # User-filter flags. Argparse's `append` action lets the caller
        # repeat the flag for multiple values (`--github-authors-allow
        # alice --github-authors-allow bob`).
        parser.add_argument(
            "--github-authors-allow", action="append", default=[],
            help="only include issues whose creator is in this list (repeatable)",
        )
        parser.add_argument(
            "--github-authors-block", action="append", default=[],
            help="exclude issues whose creator is in this list (repeatable)",
        )
        parser.add_argument(
            "--github-assignees-allow", action="append", default=[],
            help="only include issues with an assignee in this list (repeatable)",
        )
        parser.add_argument(
            "--github-assignees-block", action="append", default=[],
            help="exclude issues with an assignee in this list (repeatable)",
        )

    def build_source(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> Dict[str, Any]:
        config: Dict[str, Any] = {
            # Backend connector reads source.config["repo"] verbatim as
            # the URL fragment, so it must be "<owner>/<name>".
            "owner": args.owner,
            "repo": f"{args.owner}/{args.repo}",
            "include": "open",
        }
        # Only emit user-filter fields when they're non-empty — keeps the
        # source.config tidy and minimises diff noise on `briar apply`.
        for key, values in _user_filters(args).items():
            if values:
                config[key] = values

        return {
            "key": f"{key_prefix}-gh-issues",
            "name": f"{key_prefix}-gh-issues",
            "kind": "github",
            "config": config,
            **_gh_auth(args),
        }

    def build_tools(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> List[Dict[str, Any]]:
        auth = _gh_auth(args)
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
