"""Raw HTTP escape hatch: `briar api <METHOD> <PATH>`."""

from __future__ import annotations

import argparse

from briar.commands.base import Command
from briar.fields import load_body, parse_fields
from briar.formatting import render_object
from briar.http import ApiClient


class CommandApi(Command):
    name = "api"
    help = "Raw API call: briar api GET /api/v1/anything/"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "method", choices=["GET", "POST", "PATCH", "PUT", "DELETE"],
        )
        parser.add_argument("path", help="path beginning with /api/...")
        parser.add_argument("--from-file", help="JSON body from file")
        parser.add_argument(
            "--field", action="append",
            help="key=value (repeatable; merged into body)",
        )
        parser.add_argument(
            "--query", action="append", default=[],
            help="key=value query param (repeatable)",
        )

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        body = load_body(args) or None
        query = parse_fields(args.query)
        payload = client.request(args.method, args.path, body=body, query=query)
        render_object(payload, args.format)
        return 0
