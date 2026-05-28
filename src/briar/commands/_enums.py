"""Closed enumerations for the CLI command surface.

Per ARCHITECTURE_MAP.md §21: enums for closed domain sets, registries
for open plug-in spaces. Exit codes are intrinsically a finite set
fixed by the CLI contract — they belong in an enum.
"""
from __future__ import annotations

from enum import IntEnum


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
