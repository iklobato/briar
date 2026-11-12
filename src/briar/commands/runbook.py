"""`briar runbook` — multi-company knowledge extraction from YAML.

Subcommands:
  extract   one-shot run (optionally filtered to a `--task`)
  serve     long-lived in-process scheduler, replaces cron

`sweep` and `serve --tick` left in 2.0 (decision C14): run `serve DIR`, or
`extract` once per file; `serve` ticks every second.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Dict

import typer

from briar.commands.base import Command, CommandOptions, Route
from briar.errors import CliError
from briar.formatting import render
from briar.iac.runbook import RunbookScheduler, extract_runbook, load_runbook_file

log = logging.getLogger(__name__)


class CommandRunbook(Command):
    name = "runbook"
    help = "Run extractors and/or serve the in-process scheduler."

    _ACTIONS: ClassVar[Dict[str, str]] = {
        "extract": "_extract",
        "serve": "_serve",
    }

    OP_HELP = {
        "extract": "walk a YAML's `extract:` or `schedules:` and write knowledge files",
        "serve": "long-running scheduler: registers every (company, task) and runs the schedule loop forever",
    }

    def run(self, args: argparse.Namespace) -> int:
        # Operational commands — surface INFO progress even though the CLI
        # is quiet-by-default (see briar.logging.daemon_logging).
        from briar.logging import daemon_logging

        daemon_logging()
        handler_name = self._ACTIONS.get(args.op)
        if handler_name is None:
            known = ", ".join(sorted(self._ACTIONS.keys()))
            raise CliError(f"unknown runbook op {args.op!r} (known: {known})")
        return getattr(self, handler_name)(args)

    def _extract(self, args: argparse.Namespace) -> int:
        runbook = load_runbook_file(Path(args.file))
        rows = extract_runbook(runbook, args.task)
        items = [{"company": r.company, "task": r.task, "status": r.status, "output": r.output} for r in rows]
        render(items, args.format, ["company", "task", "status", "output"])
        return 0

    def _serve(self, args: argparse.Namespace) -> int:
        directory = Path(args.directory)
        if not directory.is_dir():
            raise CliError(f"serve: {directory} is not a directory")
        scheduler = RunbookScheduler(directory)
        for entry in scheduler.register_all():
            log.info("registered company=%s task=%s every=%r", entry.company, entry.task, entry.every)
        scheduler.run_forever(TICK_SECONDS)
        return 0


# ─── Options on Typer ──────────────────────────────────────────────

# C14: the 1.x `--tick` left; `serve` checks the schedule every second.
TICK_SECONDS = 1.0


@dataclass(frozen=True)
class RunbookExtractOptions(CommandOptions):
    ROUTE = Route(CommandRunbook.name, "op", "extract")

    file: str = typer.Argument(...)
    task: str = typer.Option("", "--task", help="run only the schedule whose `task:` matches this name")


@dataclass(frozen=True)
class RunbookServeOptions(CommandOptions):
    ROUTE = Route(CommandRunbook.name, "op", "serve")

    directory: str = typer.Argument(...)
