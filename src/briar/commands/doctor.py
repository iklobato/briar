"""`briar doctor`: offline environment health check.

Prints the settings table (what `briar config show` printed in 1.x, plus the
telemetry tier), then one status line per check (python, config, git,
credentials), and exits non-zero only on a hard failure, so it is CI-usable.
With `--format json` it prints `{"settings": [...], "checks": [...]}`
(decisions C16, F2, F3)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Dict, List, Mapping, Optional

from briar.commands._enums import ExitCode
from briar.commands.base import Command, CommandOptions, Route


class TelemetryRow:
    """The telemetry tier as a settings row, from the config `briar.cli.main` resolved
    at startup (F3). Reading it does not resolve it again, so it writes no install id."""

    SOURCES: ClassVar[Mapping[str, str]] = {
        "do-not-track": "env (DO_NOT_TRACK)",
        "env": "env (BRIAR_TELEMETRY)",
        "project-config": "config ({file})",
        "default": "default",
    }

    def __init__(self, config_file: str) -> None:
        self._config_file = config_file

    def row(self) -> Dict[str, str]:
        from briar import telemetry

        resolved = telemetry.active_config()
        if resolved is None:
            return {"setting": "telemetry", "value": "(unknown)", "source": "-"}
        source = self.SOURCES.get(resolved.source, resolved.source).format(file=self._config_file)
        return {"setting": "telemetry", "value": resolved.tier.value, "source": source}


@dataclass(frozen=True)
class DoctorReport:
    """The two tables `doctor` prints."""

    SETTING_COLUMNS: ClassVar[List[str]] = ["setting", "value", "source"]
    CHECK_COLUMNS: ClassVar[List[str]] = ["check", "status", "detail"]
    JSON = "json"

    settings: List[Dict[str, str]]
    checks: List[Dict[str, str]]

    def render(self, format_name: str) -> None:
        from briar.formatting import render

        if format_name == self.JSON:
            render({"settings": self.settings, "checks": self.checks}, format_name)
            return
        render(self.settings, format_name, self.SETTING_COLUMNS)
        render(self.checks, format_name, self.CHECK_COLUMNS)


class CommandDoctor(Command):
    name = "doctor"
    help = "Show the settings and check the local environment (config, git, credentials)."

    def run(self, args: argparse.Namespace) -> int:
        from briar.doctor import FAIL, run_checks, worst_status

        checks = run_checks()
        report = DoctorReport(self._settings(), [{"check": c.name, "status": c.status, "detail": c.detail} for c in checks])
        report.render(args.format)
        return ExitCode.GENERAL_ERROR if worst_status(checks) == FAIL else ExitCode.OK

    def _settings(self) -> List[Dict[str, str]]:
        from briar.config import find_config_file, load_project_config
        from briar.settings import SettingsResolver

        path: Optional[Path] = find_config_file()
        sys.stderr.write(f"config file: {path if path else '(none found: using env / git / defaults)'}\n")
        rows = [item.row() for item in SettingsResolver.from_project(load_project_config()).resolved()]
        return [*rows, TelemetryRow(path.name if path else "").row()]


@dataclass(frozen=True)
class DoctorOptions(CommandOptions):
    ROUTE = Route(CommandDoctor.name)
