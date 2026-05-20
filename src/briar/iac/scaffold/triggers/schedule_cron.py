"""Cron-schedule trigger — fires the workflow on a cron expression."""

from __future__ import annotations

import argparse
from typing import Any, Dict

from briar.iac.scaffold.triggers.base import TriggerTemplate


class TriggerScheduleCron(TriggerTemplate):
    kind = "schedule_cron"
    description = "Fires on a cron schedule (default: hourly)"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--schedule",
            default="0 * * * *",
            help="cron expression (default: '0 * * * *' = top of every hour)",
        )

    def build_trigger(
        self,
        args: argparse.Namespace,
        key_prefix: str,
        workflow_key: str,
    ) -> Dict[str, Any]:
        return {
            "key": f"{key_prefix}-cron",
            "name": f"{key_prefix}-cron",
            "kind": "schedule",
            "workflow_key": workflow_key,
            "schedule_cron": args.schedule,
            "filter_rules": {},
            "payload_to_context_mapping": {},
            "is_enabled": True,
        }
