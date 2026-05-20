"""`briar runbook` — multi-company knowledge extraction from a YAML file.

After v2.0 the only operation is `extract`. Apply/plan/destroy were
removed when the CLI dropped its API surface — backend resources are
managed from the web UI."""

from __future__ import annotations

import argparse
from pathlib import Path

from briar.commands.base import Command
from briar.errors import CliError
from briar.formatting import render
from briar.iac.runbook import extract_runbook, load_runbook_file


class CommandRunbook(Command):
    name = "runbook"
    help = "Run extractors for every company declared in a YAML file."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="op", required=True)

        ex = sub.add_parser(
            "extract",
            help="walk each company's `extract:` and write its knowledge_file",
        )
        ex.add_argument("file")

        sw = sub.add_parser(
            "sweep",
            help="run extract for every *.yaml in a directory",
        )
        sw.add_argument("directory", help="folder containing runbook YAMLs")

    def run(self, args: argparse.Namespace) -> int:
        if args.op == "extract":
            return self._extract(args)
        if args.op == "sweep":
            return self._sweep(args)
        raise CliError(f"unknown runbook op {args.op!r}")

    def _extract(self, args: argparse.Namespace) -> int:
        runbook = load_runbook_file(Path(args.file))
        rows = extract_runbook(runbook)
        items = [{"company": c, "status": s, "output": o} for c, s, o in rows]
        render(items, args.format, ["company", "status", "output"])
        return 0

    def _sweep(self, args: argparse.Namespace) -> int:
        root = Path(args.directory)
        if not root.is_dir():
            raise CliError(f"sweep: {root} is not a directory")
        files = sorted(p for p in root.glob("*.yaml") if p.is_file())
        if not files:
            print(f"sweep: no *.yaml under {root}")
            return 0
        exit_code = 0
        for path in files:
            print(f"--- {path.name} ---")
            try:
                runbook = load_runbook_file(path)
                rows = extract_runbook(runbook)
                render(
                    [{"company": c, "status": s, "output": o} for c, s, o in rows],
                    args.format, ["company", "status", "output"],
                )
            except Exception as exc:  # noqa: BLE001 - cron must not abort the loop
                print(f"FAILED {path.name}: {exc}")
                exit_code = 1
        return exit_code
