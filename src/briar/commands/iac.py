"""IaC scaffold command — emits JSON the user pastes into the web UI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from briar.commands.base import Command
from briar.errors import CliError
from briar.iac import TEMPLATES


class CommandScaffold(Command):
    name = "scaffold"
    help = "Generate a starter config file for a built-in template."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(
            dest="template", required=True, metavar="TEMPLATE",
        )
        for name, tmpl in TEMPLATES.items():
            tp = sub.add_parser(name, help=tmpl.description)
            tp.add_argument(
                "--out", "-o", default="-",
                help="output path (default: stdout)",
            )
            tmpl.add_arguments(tp)

    def run(self, args: argparse.Namespace) -> int:
        tmpl = TEMPLATES.get(args.template)
        if tmpl is None:
            raise CliError(f"unknown template: {args.template}")
        text = json.dumps(tmpl.build(args), indent=2)
        if args.out == "-":
            print(text)
            return 0
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
        return 0
