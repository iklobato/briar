"""Tasks command — CRUD + cancel/retry/runs/checkpoints."""

from __future__ import annotations

import argparse

from briar.commands.resource import HandlerMap, CommandResource
from briar.formatting import render, render_object
from briar.http import ApiClient


class CommandTasks(CommandResource):
    name = "tasks"
    help = "Manage tasks (+ cancel / retry / runs / checkpoints)."
    base_path = "/api/v1/tasks/"
    columns = ["id", "title", "status", "workflow", "created_at"]

    def _add_extras(self, sub: argparse._SubParsersAction) -> None:
        for verb in ("cancel", "retry"):
            sp = sub.add_parser(verb, help=f"{verb} a task")
            sp.add_argument("id")
        for verb in ("runs", "checkpoints"):
            sp = sub.add_parser(verb, help=f"list {verb} for a task")
            sp.add_argument("id")

    def _extra_handlers(self) -> HandlerMap:
        return {
            "cancel":      self._cancel,
            "retry":       self._retry,
            "runs":        self._runs,
            "checkpoints": self._checkpoints,
        }

    def _cancel(self, client: ApiClient, args: argparse.Namespace) -> int:
        payload = client.request("POST", f"{self.base_path}{args.id}/cancel/")
        render_object(payload, args.format)
        return 0

    def _retry(self, client: ApiClient, args: argparse.Namespace) -> int:
        payload = client.request("POST", f"{self.base_path}{args.id}/retry/")
        render_object(payload, args.format)
        return 0

    def _runs(self, client: ApiClient, args: argparse.Namespace) -> int:
        items = client.list_all(f"{self.base_path}{args.id}/runs/")
        render(items, args.format)
        return 0

    def _checkpoints(
        self,
        client: ApiClient,
        args: argparse.Namespace,
    ) -> int:
        # CheckpointViewSet has no ?task= filter — list + narrow client-side.
        items = client.list_all("/api/v1/checkpoints/")
        narrowed = [
            c for c in items
            if type(c) is dict and c.get("task") == args.id
        ]
        render(narrowed, args.format)
        return 0
