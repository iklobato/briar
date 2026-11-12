"""`briar runbook` — multi-company knowledge extraction from YAML.

Subcommands:
  extract   one-shot run (optionally filtered to a `--task`)
  sweep     one-shot over every YAML in a directory
  serve     long-lived in-process scheduler — replaces cron
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
        "sweep": "_sweep",
        "serve": "_serve",
    }

    OP_HELP = {
        "extract": "walk a YAML's `extract:` or `schedules:` and write knowledge files",
        "sweep": "run extract for every *.yaml in a directory",
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
            log.info("sweep: processing %s", path.name)
            print(f"--- {path.name} ---")
            try:
                runbook = load_runbook_file(path)
                rows = extract_runbook(runbook)
                items = [{"company": r.company, "task": r.task, "status": r.status, "output": r.output} for r in rows]
                render(items, args.format, ["company", "task", "status", "output"])
            except Exception:  # noqa: BLE001
                log.exception("sweep: %s failed", path.name)
                exit_code = 1
        return exit_code

    def _serve(self, args: argparse.Namespace) -> int:
        directory = Path(args.directory)
        if not directory.is_dir():
            raise CliError(f"serve: {directory} is not a directory")
        scheduler = RunbookScheduler(directory)
        for entry in scheduler.register_all():
            log.info("registered company=%s task=%s every=%r", entry.company, entry.task, entry.every)
        scheduler.run_forever(args.tick)
        return 0


# ─── Options on Typer ──────────────────────────────────────────────

DEFAULT_TICK_SECONDS = 1.0


@dataclass(frozen=True)
class RunbookExtractOptions(CommandOptions):
    ROUTE = Route(CommandRunbook.name, "op", "extract")

    file: str = typer.Argument(...)
    task: str = typer.Option("", "--task", help="run only the schedule whose `task:` matches this name")


@dataclass(frozen=True)
class RunbookSweepOptions(CommandOptions):
    ROUTE = Route(CommandRunbook.name, "op", "sweep")

    directory: str = typer.Argument(...)


@dataclass(frozen=True)
class RunbookServeOptions(CommandOptions):
    ROUTE = Route(CommandRunbook.name, "op", "serve")

    directory: str = typer.Argument(...)
    tick: float = typer.Option(DEFAULT_TICK_SECONDS, "--tick", help="seconds between schedule.run_pending() ticks (default: 1)")
