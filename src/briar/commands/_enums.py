"""Closed enumerations for the CLI command surface.

Per ARCHITECTURE_MAP.md §21: enums for closed domain sets, registries
for open plug-in spaces. Exit codes are intrinsically a finite set
fixed by the CLI contract — they belong in an enum.
"""
from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """CLI process exit codes returned by Command.run.

    Conventions:
    - 0    success
    - 1    general error
    - 2    usage error (argparse-compatible)
    - 3-9  pre-LLM failures (store / clone / git / agent setup)
    - 10+  reserved for future LLM/agent runtime failures
    """

    OK = 0
    GENERAL_ERROR = 1
    USAGE_ERROR = 2
    STORE_OPEN_FAILED = 3
    CLONE_FAILED = 4
    GIT_CONFIG_FAILED = 5
    AGENT_ERROR = 6
