"""Memberships — nested resource under /workspaces/<ws>/memberships/."""

from __future__ import annotations

import argparse
from typing import Callable, Dict

from briar.commands.base import Command, confirm
from briar.fields import load_body
from briar.formatting import render, render_object
from briar.http import ApiClient


Handler = Callable[[ApiClient, argparse.Namespace], int]


class CommandMemberships(Command):
    name = "memberships"
    help = (
        "Manage workspace memberships "
        "(nested under /workspaces/<ws>/memberships/)."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("workspace_id", help="workspace UUID")
        sub = parser.add_subparsers(dest="op", required=True)

        sub.add_parser("list", help="list memberships in the workspace")

        ad = sub.add_parser("add", help="add a member")
        ad.add_argument("--from-file")
        ad.add_argument(
            "--field", action="append",
            help="e.g. --field user=<uuid> --field role=editor",
        )

        pt = sub.add_parser("patch", help="update a membership (e.g. role)")
        pt.add_argument("id")
        pt.add_argument("--from-file")
        pt.add_argument("--field", action="append")

        rm = sub.add_parser("remove", help="remove a membership")
        rm.add_argument("id")
        rm.add_argument("--yes", action="store_true")

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        handlers: Dict[str, Handler] = {
            "list":   self._list,
            "add":    self._add,
            "patch":  self._patch,
            "remove": self._remove,
        }
        return handlers[args.op](client, args)

    def _base(self, ws_id: str) -> str:
        return f"/api/v1/workspaces/{ws_id}/memberships/"

    def _list(self, client: ApiClient, args: argparse.Namespace) -> int:
        items = client.list_all(self._base(args.workspace_id))
        render(items, args.format)
        return 0

    def _add(self, client: ApiClient, args: argparse.Namespace) -> int:
        body = load_body(args)
        payload = client.request("POST", self._base(args.workspace_id), body)
        render_object(payload, args.format)
        return 0

    def _patch(self, client: ApiClient, args: argparse.Namespace) -> int:
        body = load_body(args)
        payload = client.request(
            "PATCH",
            f"{self._base(args.workspace_id)}{args.id}/",
            body,
        )
        render_object(payload, args.format)
        return 0

    def _remove(self, client: ApiClient, args: argparse.Namespace) -> int:
        ok = bool(args.yes) or confirm(
            f"Remove membership {args.id}? [y/N] "
        )
        if not ok:
            print("aborted")
            return 1
        client.request(
            "DELETE",
            f"{self._base(args.workspace_id)}{args.id}/",
        )
        print(f"removed membership {args.id}")
        return 0
