"""Company logins for adapter tests (spec 005): an `[auth]` table and a plain
environment dict instead of `.briar.toml` and `secrets.env`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from briar.auth.check import LoginRejected, LoginTarget, SourceUnreachable
from briar.auth.config import AuthTable
from briar.auth.logins import CompanyLogins, GithubLogin, JiraLogin, LoginScope
from briar.credentials._paths import secrets_path


class Fixes:
    """The fix a missing login names since `briar auth` left (spec 003 part 4, X4): the
    `BRIAR_*` names, where to write them (the `secrets.env` of the test), and how to get one."""

    def secrets(self, names: List[str], how: str) -> str:
        return f"set {' and '.join(names)} in the environment or in {secrets_path()} ({how})"

    def github(self) -> str:
        return self.secrets(["BRIAR_ACME_GITHUB_TOKEN"], GithubLogin.HELP["token"])

    def jira(self) -> str:
        return self.secrets(["BRIAR_ACME_JIRA_EMAIL", "BRIAR_ACME_JIRA_TOKEN"], JiraLogin.HELP["token"])


@dataclass(frozen=True)
class FakeLogins:
    auth: Dict[str, Any] = field(default_factory=dict)
    environ: Dict[str, str] = field(default_factory=dict)
    company: str = "acme"

    def logins(self) -> CompanyLogins:
        block = AuthTable({"auth": self.auth}, ".briar.toml").block()
        return CompanyLogins(LoginScope(self.company), block, self.environ)


@dataclass(frozen=True)
class FakeProbe:
    """An adapter for the login check (spec 005 PR 3), with no network: "who am I" gives
    `account`, or raises when `refused` or `unreachable` is set; a read of a resource in
    `unreadable` raises. `calls` records what the check asked, in order."""

    login_source: str
    account: str = "bot@example.test"
    refused: str = ""
    unreachable: str = ""
    unreadable: Tuple[str, ...] = ()
    calls: List[str] = field(default_factory=list)

    def who_am_i(self) -> str:
        self.calls.append("who_am_i")
        if self.unreachable:
            raise SourceUnreachable(self.unreachable)
        if self.refused:
            raise LoginRejected(self.refused)
        return self.account

    def read(self, resource: str) -> None:
        self.calls.append(f"read {resource}")
        if resource in self.unreadable:
            raise LoginRejected("HTTP 404: Not Found")


@dataclass(frozen=True)
class FakeSources:
    """The default adapter of each source for `auth` and `doctor` tests: a `FakeProbe`,
    `ok` unless the test sets one with `probe`."""

    probes: Dict[str, FakeProbe] = field(default_factory=dict)

    def probe(self, probe: FakeProbe) -> FakeProbe:
        self.probes[probe.login_source] = probe
        return probe

    def target(self, source: str) -> LoginTarget:
        return LoginTarget(self.probes.setdefault(source, FakeProbe(source)))
