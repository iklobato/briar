"""Closed enumerations for the CLI command surface.

Enums for closed domain sets, registries for open plug-in spaces. Exit codes are intrinsically a finite set
fixed by the CLI contract — they belong in an enum.
"""
from __future__ import annotations

from enum import Enum, IntEnum


class ExitCode(IntEnum):
    """CLI process exit codes returned by Command.run.

    - 0  success
    - 1  general error — any runtime failure the operator can't fix
         via a flag (network, missing creds, agent crashed, …)
    - 2  usage error (argparse-compatible)
    """

    OK = 0
    GENERAL_ERROR = 1
    USAGE_ERROR = 2


class OutputFormat(str, Enum):
    """`--format` choices on Typer; a test keeps them equal to `briar.formatting.FORMATTERS`."""

    TABLE = "table"
    JSON = "json"


