"""Checkpoints — read + approve/reject."""

from __future__ import annotations

import argparse
from typing import Any, Dict

from briar.commands.resource import HandlerMap, CommandResource
from briar.formatting import render_object
from briar.http import ApiClient


class CommandCheckpoints(CommandResource):
    name = "checkpoints"
    help = "Approve / reject human checkpoints."
    base_path = "/api/v1/checkpoints/"
    columns = ["id", "task", "node_id", "status"]
    read_only = True  # checkpoints are created by the orchestrator, not the CLI

    def _add_extras(self, sub: argparse._SubParsersAction) -> None:
        ap = sub.add_parser("approve", help="approve a checkpoint")
        ap.add_argument("id")
        ap.add_argument("--decision", default="approve")
        ap.add_argument("--note", default="")

        rj = sub.add_parser("reject", help="reject a checkpoint")
        rj.add_argument("id")
        rj.add_argument("--note", default="")

    def _extra_handlers(self) -> HandlerMap:
        return {"approve": self._approve, "reject": self._reject}

    def _approve(self, client: ApiClient, args: argparse.Namespace) -> int:
        body: Dict[str, Any] = {"decision": args.decision}
        if args.note:
            body["note"] = args.note
        payload = client.request(
            "POST", f"{self.base_path}{args.id}/approve/", body,
        )
        render_object(payload, args.format)
        return 0

    def _reject(self, client: ApiClient, args: argparse.Namespace) -> int:
        body: Dict[str, Any] = {}
        if args.note:
            body["note"] = args.note
        payload = client.request(
            "POST", f"{self.base_path}{args.id}/reject/", body,
        )
        render_object(payload, args.format)
        return 0
