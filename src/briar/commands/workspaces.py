"""Workspace command — full CRUD plus use/show pinning helpers.

Implemented as a single composite rather than a `CommandResource`
subclass because it carries the extra pinning semantics."""

from __future__ import annotations

import argparse
from typing import Callable, Dict

from briar.commands.base import Command, confirm
from briar.errors import CliError
from briar.fields import load_body
from briar.formatting import render, render_object
from briar.http import ApiClient


Handler = Callable[[ApiClient, argparse.Namespace], int]


class CommandWorkspace(Command):
    name = "workspace"
    help = (
        "Manage workspaces "
        "(list / get / create / patch / delete / use / show)."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="op", required=True)

        sub.add_parser("list", help="list workspaces you belong to")

        gp = sub.add_parser("get", help="fetch one workspace")
        gp.add_argument("id")

        cr = sub.add_parser("create", help="create a workspace")
        cr.add_argument("--from-file")
        cr.add_argument("--field", action="append")

        up = sub.add_parser("patch", help="partial update")
        up.add_argument("id")
        up.add_argument("--from-file")
        up.add_argument("--field", action="append")

        de = sub.add_parser("delete", help="delete a workspace")
        de.add_argument("id")
        de.add_argument("--yes", action="store_true")

        use = sub.add_parser(
            "use", help="pin the workspace used by all API calls",
        )
        use.add_argument("id")

        sub.add_parser("show", help="print the currently pinned workspace id")

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        handlers: Dict[str, Handler] = {
            "list":   self._list,
            "get":    self._get,
            "create": self._create,
            "patch":  self._patch,
            "delete": self._delete,
            "use":    self._use,
            "show":   self._show,
        }
        return handlers[args.op](client, args)

    # ---- handlers --------------------------------------------------------

    def _list(self, client: ApiClient, args: argparse.Namespace) -> int:
        items = client.list_all("/api/v1/workspaces/")
        render(items, args.format)
        return 0

    def _get(self, client: ApiClient, args: argparse.Namespace) -> int:
        payload = client.request("GET", f"/api/v1/workspaces/{args.id}/")
        render_object(payload, args.format)
        return 0

    def _create(self, client: ApiClient, args: argparse.Namespace) -> int:
        body = load_body(args)
        payload = client.request("POST", "/api/v1/workspaces/", body)
        render_object(payload, args.format)
        return 0

    def _patch(self, client: ApiClient, args: argparse.Namespace) -> int:
        body = load_body(args)
        payload = client.request(
            "PATCH", f"/api/v1/workspaces/{args.id}/", body,
        )
        render_object(payload, args.format)
        return 0

    def _delete(self, client: ApiClient, args: argparse.Namespace) -> int:
        ok = bool(args.yes) or confirm(
            f"Delete workspace {args.id}? [y/N] "
        )
        if not ok:
            print("aborted")
            return 1
        client.request("DELETE", f"/api/v1/workspaces/{args.id}/")
        print(f"deleted workspace {args.id}")
        return 0

    def _use(self, client: ApiClient, args: argparse.Namespace) -> int:
        client.creds.workspace = args.id
        client.store.save()
        print(f"workspace pinned: {args.id}")
        return 0

    def _show(self, client: ApiClient, args: argparse.Namespace) -> int:
        print(client.creds.workspace or "(none)")
        return 0
