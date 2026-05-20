"""GitHub webhook trigger — fires on `issues.opened` / `issues.labeled`."""

from __future__ import annotations

import argparse
from typing import Any, Dict

from briar.iac.scaffold.triggers.base import TriggerTemplate


class TriggerGithubWebhook(TriggerTemplate):
    kind = "github_webhook"
    description = "Fires on GitHub webhook events (issues.opened, …)"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--webhook-events",
            action="append",
            default=[],
            help="Github event names (default: issues.opened, issues.labeled)",
        )
        parser.add_argument(
            "--webhook-labels",
            action="append",
            default=["briar"],
            help="Restrict to issues with any of these labels (default: briar)",
        )

    def build_trigger(
        self,
        args: argparse.Namespace,
        key_prefix: str,
        workflow_key: str,
    ) -> Dict[str, Any]:
        events = args.webhook_events or ["issues.opened", "issues.labeled"]
        return {
            "key": f"{key_prefix}-trigger",
            "name": f"{key_prefix}-trigger",
            "kind": "github_webhook",
            "workflow_key": workflow_key,
            "filter_rules": {
                "events": events,
                "labels_any": args.webhook_labels,
            },
            "payload_to_context_mapping": {
                "issue_number": "$.issue.number",
                "issue_title": "$.issue.title",
                "issue_body": "$.issue.body",
                "repo": "$.repository.full_name",
            },
            "is_enabled": True,
        }
