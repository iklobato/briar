"""`briar doctor` — offline environment health check.

Prints a status line per check (python, config, git, credentials)
and exits non-zero only on a hard failure, so it is CI-usable."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from briar.commands._enums import ExitCode
from briar.commands.base import Command, CommandOptions, Route


class CommandDoctor(Command):
    name = "doctor"
    help = "Check the local environment (config, git, credentials)."

    def run(self, args: argparse.Namespace) -> int:
        from briar.doctor import FAIL, run_checks, worst_status
        from briar.formatting import render

        checks = run_checks()
        rows = [{"check": c.name, "status": c.status, "detail": c.detail} for c in checks]
        render(rows, args.format, ["check", "status", "detail"])
        return ExitCode.GENERAL_ERROR if worst_status(checks) == FAIL else ExitCode.OK


@dataclass(frozen=True)
class DoctorOptions(CommandOptions):
    ROUTE = Route(CommandDoctor.name)
