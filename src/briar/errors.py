"""Custom exception hierarchy.

`CliError` is the user-visible base; everything raised intentionally by
this codebase should be a subclass so the entry point can render a
single-line `error: …` message and exit non-zero. Backend HTTP failures
become `ApiError`; auth-specific failures become `AuthError`; IaC
config-file failures become `ConfigError`.
"""

from __future__ import annotations

import json
from typing import Any


class CliError(Exception):
    """User-visible failure. The message is printed and the CLI exits 1."""


class AuthError(CliError):
    """Login / refresh / token failures. Raised from auth flows so the
    entry point can render a friendly message before exiting."""


class ConfigError(CliError):
    """IaC config-file failures (parse errors, missing references, …)."""


class ApiError(CliError):
    """Non-2xx HTTP response from the Briar backend."""

    def __init__(self, status: int, body: Any, method: str, path: str) -> None:
        self.status = status
        self.body = body
        self.method = method
        self.path = path
        super().__init__(
            f"{method} {path} → HTTP {status}: {self._short_body(body)}"
        )

    @staticmethod
    def _short_body(body: Any) -> str:
        """Collapse a non-JSON HTML 500 page to one line so the caller
        gets a readable error instead of a wall of `<!doctype …>`."""
        if type(body) is str:
            stripped = body.lstrip()
            if stripped[:9].lower() in {"<!doctype", "<html lan"} or stripped.startswith("<!DOCTYPE"):
                return (
                    "(server returned HTML — likely an unhandled backend "
                    "exception; check api logs)"
                )
            return body[:300]
        return json.dumps(body, default=str)[:300]
