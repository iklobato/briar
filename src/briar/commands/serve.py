"""`briar serve DIR`: the agentic development pipeline (spec 003).

Reads every runbook in DIR. For each company with a `serve:` block it polls Jira and
GitHub for the tickets assigned to the account of the token and for new review comments
on the PRs briar opened, and runs up to `max_parallel` tasks at once. It also runs the
scheduled extracts of the runbooks, as `briar runbook serve` did in 1.x; a folder with
no `serve:` block runs only those (X1). Before the first cycle it checks the model
login and the read and write access of every company, and stops (exit 1) when one
fails. Decisions: `docs/decisions/2026-11-11-briar-2-serve-pipeline.md`."""

from __future__ import annotations

import argparse
import logging
import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Sequence

import typer

from briar.commands.base import Command, CommandOptions, Route
from briar.errors import CliError

if TYPE_CHECKING:
    from briar.serve.loop import ServeLoops
    from briar.serve.work import ServeCompany


class CommandServe(Command):
    LOG: ClassVar[logging.Logger] = logging.getLogger(__name__)
    name = "serve"
    help = "Watch the assigned tickets and the PRs of briar, and run the scheduled extracts of the runbooks in DIR."

    def serve(self, args: argparse.Namespace) -> int:
        from briar.iac.runbook import RunbookScheduler
        from briar.logging import daemon_logging
        from briar.serve.loop import ServeLoops
        from briar.serve.work import ServeCompanies

        daemon_logging()
        directory = Path(args.directory)
        if not directory.is_dir():
            raise CliError(f"serve: {directory} is not a directory")
        companies = ServeCompanies(directory, os.environ).load()
        scheduler = RunbookScheduler(directory)
        if not scheduler.register_all() and not companies:
            raise CliError(f"serve: nothing to serve in {directory}: no company has a `serve:` block or a schedule")
        scheduler.check_logins()
        self._check(companies, directory)
        self._serve_locked(companies, ServeLoops(companies, scheduler))
        return 0

    def _serve_locked(self, companies: Sequence["ServeCompany"], loops: "ServeLoops") -> None:
        """One `serve` per company and knowledge folder (`ServeLock`)."""
        from briar.serve.work import ServeLock

        locks = [ServeLock(company) for company in companies]
        try:
            for lock in locks:
                lock.take()
            loops.loop().serve_forever()
        finally:
            for lock in locks:
                lock.release()

    def _check(self, companies: Sequence["ServeCompany"], directory: Path) -> None:
        """The checks of the pipeline; a folder with no `serve:` block needs none (X1)."""
        from briar.serve.work import AgentSandbox, ServeCheck

        if not companies:
            self.LOG.info("serve: no company in %s has a `serve:` block; running only the scheduled extracts", directory)
            return
        ServeCheck(companies, os.environ, AgentSandbox(platform.system(), shutil.which)).require()

    # The `Command` interface calls `run`.
    run = serve


@dataclass(frozen=True)
class ServeOptions(CommandOptions):
    ROUTE = Route(CommandServe.name)

    directory: str = typer.Argument(..., help="The folder of runbooks to serve.")
