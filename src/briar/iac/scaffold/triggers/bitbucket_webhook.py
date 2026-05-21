"""Bitbucket webhook trigger — fires on `issue:created` / `issue:updated`."""

from __future__ import annotations

import argparse
from typing import Any, Dict

from briar.iac.scaffold.triggers.base import TriggerTemplate


class TriggerBitbucketWebhook(TriggerTemplate):
    kind = "bitbucket_webhook"
    description = "Fires on Bitbucket webhook events (issue:created, …)"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--bitbucket-webhook-events",
            action="append",
            default=[],
            help="Bitbucket event names (default: issue:created, issue:updated)",
        )
        parser.add_argument(
            "--bitbucket-webhook-labels",
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
        ns = vars(args)
        events = ns.get("bitbucket_webhook_events") or ["issue:created", "issue:updated"]
        labels = ns.get("bitbucket_webhook_labels") or ["briar"]
        return {
            "key": f"{key_prefix}-trigger",
            "name": f"{key_prefix}-trigger",
            "kind": "bitbucket_webhook",
            "workflow_key": workflow_key,
            "filter_rules": {
                "events": events,
                "labels_any": labels,
            },
            "payload_to_context_mapping": {
                "issue_number": "$.issue.id",
                "issue_title": "$.issue.title",
                "issue_body": "$.issue.content.raw",
                "repo": "$.repository.full_name",
            },
            "is_enabled": True,
        }
