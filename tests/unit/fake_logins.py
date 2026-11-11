"""Company logins for adapter tests (spec 005): an `[auth]` table and a plain
environment dict instead of `.briar.toml` and `secrets.env`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from briar.auth.config import AuthTable
from briar.auth.logins import CompanyLogins, LoginScope


@dataclass(frozen=True)
class FakeLogins:
    auth: Dict[str, Any] = field(default_factory=dict)
    environ: Dict[str, str] = field(default_factory=dict)
    company: str = "acme"

    def logins(self) -> CompanyLogins:
        block = AuthTable({"auth": self.auth}, ".briar.toml").block()
        return CompanyLogins(LoginScope(self.company), block, self.environ)
