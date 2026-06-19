"""IaC scaffold command — emits JSON the user pastes into the web UI.

Wraps the build in a journal `session` so the composer's decisions
(source kinds, archetype, shape, trigger, knowledge splice) are
recorded for later inspection via `briar journal show`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from briar.commands.base import Command
from briar.errors import CliError
from briar.iac import TEMPLATES
from briar.journal import record, session


class CommandScaffold(Command):
    name = "scaffold"
    help = "Generate a starter config file for a built-in template."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(
            dest="template",
            required=True,
            metavar="TEMPLATE",
        )
        for name, tmpl in TEMPLATES.items():
            tp = sub.add_parser(name, help=tmpl.description)
            tp.add_argument(
                "--out",
                "-o",
                default="-",
                help="output path (default: stdout)",
            )
            tmpl.add_arguments(tp)

    def run(self, args: argparse.Namespace) -> int:
        tmpl = TEMPLATES.get(args.template)
        if tmpl is None:
            raise CliError(f"unknown template: {args.template}")
        with session(command=f"scaffold.{args.template}", target=getattr(args, "prefix", "")):
            record(
                "scaffold.template",
                value=args.template,
                rationale="user-selected scaffold template",
                alternatives=tuple(TEMPLATES.keys()),
            )
            bundle = tmpl.build(args)
            record(
                "scaffold.output",
                value={"agents": len(bundle.get("agents", [])), "tools": len(bundle.get("tools", [])), "sources": len(bundle.get("sources", []))},
                rationale="composer produced the bundle counts",
            )
            text = json.dumps(bundle, indent=2)
            if args.out == "-":
                print(text)
                return 0
            Path(args.out).write_text(text)
            print(f"wrote {args.out}", file=sys.stderr)
            return 0
