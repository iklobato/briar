"""`briar runbook` — multi-company knowledge extraction from YAML.

Subcommands:
  extract   one-shot run (optionally filtered to a `--task`)
  sweep     one-shot over every YAML in a directory
  serve     long-lived in-process scheduler — replaces cron
"""

from __future__ import annotations

import argparse
from pathlib import Path

from briar.commands.base import Command
from briar.errors import CliError
from briar.formatting import render
from briar.iac.runbook import (
    RunbookScheduler,
    extract_runbook,
    load_runbook_file,
)


class CommandRunbook(Command):
    name = "runbook"
    help = "Run extractors and/or serve the in-process scheduler."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="op", required=True)

        ex = sub.add_parser(
            "extract",
            help="walk a YAML's `extract:` or `schedules:` and write knowledge files",
        )
        ex.add_argument("file")
        ex.add_argument(
            "--task",
            help="run only the schedule whose `task:` matches this name",
        )

        sw = sub.add_parser(
            "sweep",
            help="run extract for every *.yaml in a directory",
        )
        sw.add_argument("directory")

        sv = sub.add_parser(
            "serve",
            help="long-running scheduler — registers every (company, task) "
                 "and runs the schedule loop forever",
        )
        sv.add_argument("directory")
        sv.add_argument(
            "--tick", type=float, default=1.0,
            help="seconds between schedule.run_pending() ticks (default: 1)",
        )

    def run(self, args: argparse.Namespace) -> int:
        if args.op == "extract":
            return self._extract(args)
        if args.op == "sweep":
            return self._sweep(args)
        if args.op == "serve":
            return self._serve(args)
        raise CliError(f"unknown runbook op {args.op!r}")

    def _extract(self, args: argparse.Namespace) -> int:
        runbook = load_runbook_file(Path(args.file))
        rows = extract_runbook(runbook, task=args.task)
        items = [
            {"company": c, "task": t, "status": s, "output": o}
            for c, t, s, o in rows
        ]
        render(items, args.format, ["company", "task", "status", "output"])
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
                items = [
                    {"company": c, "task": t, "status": s, "output": o}
                    for c, t, s, o in rows
                ]
                render(items, args.format, ["company", "task", "status", "output"])
            except Exception as exc:  # noqa: BLE001
                print(f"FAILED {path.name}: {exc}")
                exit_code = 1
        return exit_code

    def _serve(self, args: argparse.Namespace) -> int:
        directory = Path(args.directory)
        if not directory.is_dir():
            raise CliError(f"serve: {directory} is not a directory")
        scheduler = RunbookScheduler(directory)
        registered = scheduler.register_all()
        for company, task, every in registered:
            print(f"  registered  {company:20s} {task:18s} every {every}")
        scheduler.run_forever(tick_seconds=args.tick)
        return 0
