"""`briar version` — local client version + optional remote info."""

from __future__ import annotations

import argparse

from briar import __version__
from briar.commands.base import Command
from briar.errors import CliError
from briar.http import ApiClient


class CommandVersion(Command):
    name = "version"
    help = "Print client version."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--remote", action="store_true",
            help="also print the backend's OpenAPI version",
        )

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        print(f"briar-cli {__version__}")
        if not args.remote:
            return 0
        try:
            schema = client.request("GET", "/api/v1/schema/")
        except CliError as exc:
            print(f"remote: {exc}")
            return 0
        info = schema.get("info") if type(schema) is dict else None
        version = info.get("version", "?") if type(info) is dict else "?"
        title = info.get("title", "?") if type(info) is dict else "?"
        print(f"remote: {title} {version}")
        return 0
