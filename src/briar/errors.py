"""Custom exception hierarchy.

`CliError` is the user-visible base; everything raised intentionally by
this codebase should be a subclass so the entry point can render a
single-line `error: …` message and exit non-zero. Auth-specific failures
become `AuthError`; runbook config failures become `ConfigError`.
"""

from __future__ import annotations


class CliError(Exception):
    """User-visible failure. The message is printed and the CLI exits 1."""


class AuthError(CliError):
    """Login / refresh / token failures. Raised from auth flows so the
    entry point can render a friendly message before exiting."""


class ConfigError(CliError):
    """Runbook config failures (parse errors, bad schedules, missing files, …)."""
