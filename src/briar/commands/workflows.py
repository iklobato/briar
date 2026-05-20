"""Workflows + workflow templates."""

from __future__ import annotations

import argparse
from typing import Dict

from briar.commands.resource import HandlerMap, CommandResource
from briar.fields import parse_fields
from briar.formatting import render, render_object
from briar.http import ApiClient


class CommandWorkflows(CommandResource):
    name = "workflows"
    help = "Manage workflows (+ versions / fork / set-active)."
    base_path = "/api/v1/workflows/"
    columns = ["id", "name", "current_version", "updated_at"]

    def _add_extras(self, sub: argparse._SubParsersAction) -> None:
        vers = sub.add_parser("versions", help="list workflow versions")
        vers.add_argument("id")

        fk = sub.add_parser(
            "fork", help="fork a workflow into a new version chain",
        )
        fk.add_argument("id")
        fk.add_argument("--field", action="append")

        sa = sub.add_parser(
            "set-active", help="promote a specific version to active",
        )
        sa.add_argument("id")
        sa.add_argument("--version", required=True,
                        help="version UUID to activate")

    def _extra_handlers(self) -> HandlerMap:
        return {
            "versions":   self._versions,
            "fork":       self._fork,
            "set-active": self._set_active,
        }

    def _versions(self, client: ApiClient, args: argparse.Namespace) -> int:
        items = client.list_all(f"{self.base_path}{args.id}/versions/")
        render(items, args.format)
        return 0

    def _fork(self, client: ApiClient, args: argparse.Namespace) -> int:
        body = parse_fields(vars(args).get("field"))
        payload = client.request(
            "POST", f"{self.base_path}{args.id}/fork/", body or {},
        )
        render_object(payload, args.format)
        return 0

    def _set_active(self, client: ApiClient, args: argparse.Namespace) -> int:
        payload = client.request(
            "POST",
            f"{self.base_path}{args.id}/set-active/",
            {"version": args.version},
        )
        render_object(payload, args.format)
        return 0


class CommandWorkflowTemplates(CommandResource):
    name = "workflow-templates"
    help = "Workflow templates (read + fork)."
    base_path = "/api/v1/workflow-templates/"
    columns = ["id", "name", "category"]
    read_only = True

    def _add_extras(self, sub: argparse._SubParsersAction) -> None:
        fk = sub.add_parser(
            "fork", help="fork template into the current workspace",
        )
        fk.add_argument("id")
        fk.add_argument(
            "--field", action="append",
            help="optional overrides, e.g. name=copy-of-foo",
        )

    def _extra_handlers(self) -> HandlerMap:
        return {"fork": self._fork}

    def _fork(self, client: ApiClient, args: argparse.Namespace) -> int:
        body = parse_fields(vars(args).get("field"))
        payload = client.request(
            "POST", f"{self.base_path}{args.id}/fork/", body or {},
        )
        render_object(payload, args.format)
        return 0
