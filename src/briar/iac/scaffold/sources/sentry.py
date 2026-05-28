"""Sentry source template.

Family: `tracker`. Reads issues (error groups) from one or more Sentry
projects inside an org. Brings action tools for the four native Sentry
verbs: comment (note), resolve, assign, and ignore. The `comment_on_issue`
naming matches the substring `tool_filter` on the triager archetype so
Sentry composes with the existing archetype filters without changes.

Auth: PAT-only for now. Sentry OAuth requires a backend integration that
isn't wired yet — `_auth` always demands `--sentry-secret-id`. Slotting
in OAuth later means adding the `credential_binding={"kind":
"oauth_connection", "provider": "sentry"}` branch and registering
`default_provider_for_oauth`."""

from __future__ import annotations

import argparse
from typing import Any, Dict, List

from briar.errors import ConfigError
from briar.iac.scaffold.sources.base import SourceTemplate


class SourceSentry(SourceTemplate):
    kind = "sentry"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--sentry-org",
            help="Sentry organization slug (required when --source includes sentry)",
        )
        parser.add_argument(
            "--sentry-project",
            action="append",
            default=[],
            help="Sentry project slug to include (repeatable; at least one required)",
        )
        parser.add_argument(
            "--sentry-environment",
            action="append",
            default=[],
            help="Restrict to one or more environments (repeatable)",
        )
        parser.add_argument(
            "--sentry-query",
            help="Sentry issue search query (e.g. 'is:unresolved level:error')",
        )
        parser.add_argument(
            "--sentry-level",
            action="append",
            default=[],
            help="Severity filter: fatal | error | warning | info | debug (repeatable)",
        )
        parser.add_argument(
            "--sentry-secret-id",
            help="Secret UUID holding a Sentry auth token (required — PAT-only for now)",
        )

    def build_source(
        self,
        args: argparse.Namespace,
        key_prefix: str,
    ) -> Dict[str, Any]:
        ns = vars(args)
        org = ns.get("sentry_org")
        projects = list(ns.get("sentry_project") or [])
        if not org or not projects:
            raise ConfigError("--source sentry requires --sentry-org AND at least one --sentry-project")

        config: Dict[str, Any] = {
            "org": org,
            "projects": projects,
            "include": "unresolved",
        }
        environments = list(ns.get("sentry_environment") or [])
        if environments:
            config["environments"] = environments
        levels = list(ns.get("sentry_level") or [])
        if levels:
            config["levels"] = levels
        query = ns.get("sentry_query")
        if query:
            config["query"] = query

        return {
            "key": f"{key_prefix}-sentry",
            "name": f"{key_prefix}-sentry",
            "kind": "sentry",
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
                "key": f"{key_prefix}-sentry-comment",
                "name": f"{key_prefix}-sentry-comment",
                "description": "Add a note (comment) to a Sentry issue",
                "implementation_ref": "sentry.comment_on_issue",
                "side_effect": "mutate",
                **auth,
            },
            {
                "key": f"{key_prefix}-sentry-resolve",
                "name": f"{key_prefix}-sentry-resolve",
                "description": "Mark a Sentry issue as resolved",
                "implementation_ref": "sentry.resolve_issue",
                "side_effect": "mutate",
                **auth,
            },
            {
                "key": f"{key_prefix}-sentry-assign",
                "name": f"{key_prefix}-sentry-assign",
                "description": "Assign a Sentry issue to a user or team",
                "implementation_ref": "sentry.assign_issue",
                "side_effect": "mutate",
                **auth,
            },
            {
                "key": f"{key_prefix}-sentry-ignore",
                "name": f"{key_prefix}-sentry-ignore",
                "description": "Ignore a Sentry issue (optionally until N events or for a window)",
                "implementation_ref": "sentry.ignore_issue",
                "side_effect": "mutate",
                **auth,
            },
        ]

    def target(self, args: argparse.Namespace) -> str:
        ns = vars(args)
        org = ns.get("sentry_org") or ""
        projects = list(ns.get("sentry_project") or [])
        if not org or not projects:
            return ""
        return f"{org}/{projects[0]}"

    @staticmethod
    def _auth(args: argparse.Namespace) -> Dict[str, Any]:
        secret_id = vars(args).get("sentry_secret_id")
        if not secret_id:
            raise ConfigError("--source sentry requires --sentry-secret-id <secret-uuid> (Sentry OAuth not yet supported)")
        return {"credentials_ref": secret_id, "credential_binding": None}
