"""OAuth providers + connections."""

from __future__ import annotations

import argparse
from typing import Callable, Dict

from briar.commands.base import Command, confirm
from briar.formatting import render, render_object
from briar.http import ApiClient
from briar.pagination import items_of


Handler = Callable[[ApiClient, argparse.Namespace], int]


class CommandOauth(Command):
    name = "oauth"
    help = "OAuth providers + connections."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="op", required=True)

        sub.add_parser("providers", help="list supported provider kinds")
        sub.add_parser("connections", help="list connected accounts")

        st = sub.add_parser("start", help="kick off an OAuth authorize flow")
        st.add_argument(
            "kind", help="provider kind (e.g. github, atlassian, slack)",
        )

        rf = sub.add_parser("refresh", help="refresh a connection's tokens")
        rf.add_argument("id")

        dc = sub.add_parser("disconnect", help="delete a connection")
        dc.add_argument("id")
        dc.add_argument("--yes", action="store_true")

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        handlers: Dict[str, Handler] = {
            "providers":   self._providers,
            "connections": self._connections,
            "start":       self._start,
            "refresh":     self._refresh,
            "disconnect":  self._disconnect,
        }
        return handlers[args.op](client, args)

    def _providers(self, client: ApiClient, args: argparse.Namespace) -> int:
        payload = client.request("GET", "/api/v1/oauth/providers/")
        render(items_of(payload), args.format)
        return 0

    def _connections(self, client: ApiClient, args: argparse.Namespace) -> int:
        items = client.list_all("/api/v1/oauth/connections/")
        cols = ["id", "provider_kind", "external_account_id", "expires_at"]
        render(items, args.format, cols)
        return 0

    def _start(self, client: ApiClient, args: argparse.Namespace) -> int:
        payload = client.request(
            "POST", f"/api/v1/oauth/{args.kind}/start/", {},
        )
        url = (
            payload.get("authorize_url", "") if type(payload) is dict else ""
        )
        if not url:
            render_object(payload, args.format)
            return 1
        print("open this URL in your browser to authorize:")
        print(url)
        return 0

    def _refresh(self, client: ApiClient, args: argparse.Namespace) -> int:
        payload = client.request(
            "POST", f"/api/v1/oauth/connections/{args.id}/refresh/", {},
        )
        render_object(payload, args.format)
        return 0

    def _disconnect(self, client: ApiClient, args: argparse.Namespace) -> int:
        ok = bool(args.yes) or confirm(
            f"Disconnect oauth connection {args.id}? [y/N] "
        )
        if not ok:
            print("aborted")
            return 1
        client.request("DELETE", f"/api/v1/oauth/connections/{args.id}/")
        print(f"disconnected {args.id}")
        return 0
