"""`briar profile list / show / use / remove` — multi-tenant config."""

from __future__ import annotations

import argparse
from typing import Callable, Dict

from briar.commands.base import Command, confirm
from briar.errors import CliError
from briar.formatting import render
from briar.http import ApiClient
from briar.profile import config_path_for, list_profiles
from briar.settings import ACTIVE_FILE, CONFIG_DIR


Handler = Callable[[ApiClient, argparse.Namespace], int]


class CommandProfile(Command):
    name = "profile"
    help = "Manage local profiles (multi-tenant: one company per profile)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="op", required=True)
        sub.add_parser("list", help="list profiles on disk")
        sub.add_parser("show", help="print the active profile name")
        use = sub.add_parser(
            "use", help="make a profile the default for new shells",
        )
        use.add_argument("name")
        rm = sub.add_parser(
            "remove", help="delete a profile's config directory",
        )
        rm.add_argument("name")
        rm.add_argument("--yes", action="store_true")

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        handlers: Dict[str, Handler] = {
            "list":   self._list,
            "show":   self._show,
            "use":    self._use,
            "remove": self._remove,
        }
        return handlers[args.op](client, args)

    def _list(self, client: ApiClient, args: argparse.Namespace) -> int:
        active = client.store.path.parent.name
        rows = [
            {"profile": name, "active": (name == active)}
            for name in list_profiles()
        ]
        render(rows, args.format, ["profile", "active"])
        return 0

    def _show(self, client: ApiClient, args: argparse.Namespace) -> int:
        print(client.store.path.parent.name)
        return 0

    def _use(self, client: ApiClient, args: argparse.Namespace) -> int:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        ACTIVE_FILE.write_text(args.name)
        print(f"active profile: {args.name}")
        print(f"(config file will be {config_path_for(args.name)})")
        return 0

    def _remove(self, client: ApiClient, args: argparse.Namespace) -> int:
        target = CONFIG_DIR / args.name
        if not target.exists():
            raise CliError(f"no such profile: {args.name}")
        ok = bool(args.yes) or confirm(
            f"Delete profile '{args.name}'? [y/N] "
        )
        if not ok:
            print("aborted")
            return 1
        for child in target.iterdir():
            child.unlink()
        target.rmdir()
        print(f"removed profile {args.name}")
        return 0
