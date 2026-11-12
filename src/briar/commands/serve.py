"""`briar serve DIR`: the agentic development pipeline (spec 003).

Reads every runbook in DIR. For each company with a `serve:` block it polls Jira and
GitHub for the tickets assigned to the account of the token and for new review comments
on the PRs briar opened, and runs up to `max_parallel` tasks at once. It also runs the
scheduled extracts of the runbooks, as `briar runbook serve` does. Before the first
cycle it checks the model login and the read and write access of every company, and
stops (exit 1) when one fails. Decisions: `docs/decisions/2026-11-11-briar-2-serve-pipeline.md`."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

import typer

from briar.commands.base import Command, CommandOptions, Route
from briar.errors import CliError


class CommandServe(Command):
    name = "serve"
    help = "Watch the assigned tickets and the PRs of briar, and run the scheduled extracts of the runbooks in DIR."

    def serve(self, args: argparse.Namespace) -> int:
        from briar.iac.runbook import RunbookScheduler
        from briar.logging import daemon_logging
        from briar.serve.loop import ServeLoops
        from briar.serve.work import AgentSandbox, ServeCheck, ServeCompanies

        daemon_logging()
        directory = Path(args.directory)
        if not directory.is_dir():
            raise CliError(f"serve: {directory} is not a directory")
        companies = ServeCompanies(directory, os.environ).load()
        if not companies:
            raise CliError(f"serve: no runbook in {directory} has a company with a `serve:` block")
        scheduler = RunbookScheduler(directory)
        scheduler.register_all()
        scheduler.check_logins()
        ServeCheck(companies, os.environ, AgentSandbox(platform.system(), shutil.which)).require()
        ServeLoops(companies, scheduler).loop().serve_forever()
        return 0

    # The `Command` interface calls `run`.
    run = serve


@dataclass(frozen=True)
class ServeOptions(CommandOptions):
    ROUTE = Route(CommandServe.name)

    directory: str = typer.Argument(..., help="The folder of runbooks to serve.")
