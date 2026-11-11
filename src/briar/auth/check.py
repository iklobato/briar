"""The login check of spec 005 (G12 to G15; decisions J1 to J19 in
`docs/decisions/2026-11-11-briar-2-logins-pr3.md`).

For each source a run uses, the check takes the login of the company, asks the
source "who am I" and reads each resource the run will read, through the same
client the run uses (J4). Adapters raise `LoginRejected` or `SourceUnreachable`;
nothing here knows a vendor library."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Dict, List, Optional, Protocol, Sequence, Tuple

from briar.auth.logins import CompanyLogins, SourceLogin
from briar.errors import AuthError, CliError


class CheckState(Enum):
    OK = "ok"
    FAILED = "failed"
    UNREACHABLE = "unreachable"


class LoginRejected(AuthError):
    """The source refused the login or the read (401, 403, 404, an expired login, a
    resource it cannot see). The message never holds a secret."""


class SourceUnreachable(AuthError):
    """The source could not be reached (timeout, no connection)."""


class LoginProbe(Protocol):
    """What an adapter gives the check: its source, "who am I", and one read."""

    login_source: ClassVar[str]

    def who_am_i(self) -> str: ...

    def read(self, resource: str) -> None: ...


@dataclass(frozen=True)
class LoginTarget:
    """One adapter a run uses and the resources (repos, projects, teams) it reads."""

    probe: LoginProbe
    resources: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckResult:
    """One line of the check: a source of a company, its state, and what to do."""

    source: str
    company: str
    state: CheckState
    detail: str
    fix: str = ""

    def passed(self) -> bool:
        return self.state is CheckState.OK

    def unreachable(self) -> bool:
        return self.state is CheckState.UNREACHABLE

    def line(self) -> str:
        text = f"{self.source} ({self.company}): {self.detail}"
        return f"{text}; {self.fix}" if self.fix else text


class LoginCheckFailed(AuthError):
    """A run stops before any work: every failed line, each with its fix (G13)."""

    HEADER = "the login check failed; fix these, then run again:"

    @classmethod
    def from_results(cls, results: Sequence[CheckResult]) -> "LoginCheckFailed":
        lines = "\n".join(f"  - {failed.line()}" for failed in results if not failed.passed())
        return cls(f"{cls.HEADER}\n{lines}")


class SourceCheck:
    """Checks one source of one company: the login is there, who am I, then each resource."""

    def __init__(self, login: SourceLogin, probe: LoginProbe) -> None:
        self._login = login
        self._probe = probe

    def results(self, resources: Sequence[str]) -> List[CheckResult]:
        problem = self._login.problem()
        if problem:
            return [self._result(CheckState.FAILED, problem)]
        try:
            account = self._probe.who_am_i()
        except SourceUnreachable as error:
            return [self._result(CheckState.UNREACHABLE, f"could not reach it ({error})")]
        except LoginRejected as error:
            return [self._result(CheckState.FAILED, f"the login was refused ({error})", self._login.login_command())]
        except CliError as error:
            # Not the answer of the source: a missing SDK or setting, whose message says what to do.
            return [self._result(CheckState.FAILED, f"could not check it: {error}")]
        found = [self._read(resource, account) for resource in resources]
        failures = [failure for failure in found if failure is not None]
        return failures or [self._result(CheckState.OK, account)]

    def _read(self, resource: str, account: str) -> Optional[CheckResult]:
        try:
            self._probe.read(resource)
        except SourceUnreachable as error:
            return self._result(CheckState.UNREACHABLE, f"could not reach it to read {resource} ({error})")
        except CliError as error:
            return self._result(CheckState.FAILED, f"{account} cannot read {resource} ({error})", "check the name and the access of this login")
        return None

    def _result(self, state: CheckState, detail: str, fix: str = "") -> CheckResult:
        return CheckResult(self._login.SOURCE, self._login.company(), state, detail, fix)


class TargetGroups:
    """The targets of a run by source: the first adapter of each source, and every
    resource of that source once, in order (J5)."""

    def __init__(self, targets: Sequence[LoginTarget]) -> None:
        self._targets = targets

    def probes(self) -> Dict[str, LoginProbe]:
        found: Dict[str, LoginProbe] = {}
        for target in self._targets:
            found.setdefault(target.probe.login_source, target.probe)
        return found

    def resources(self, source: str) -> List[str]:
        names = [resource for target in self._targets if target.probe.login_source == source for resource in target.resources]
        return list(dict.fromkeys(names))


class LoginCheck:
    """Runs the check for the targets of one company, one call after another (J6)."""

    def __init__(self, logins: CompanyLogins) -> None:
        self._logins = logins

    def check(self, targets: Sequence[LoginTarget]) -> List[CheckResult]:
        groups = TargetGroups(targets)
        lines: List[CheckResult] = []
        for source, probe in groups.probes().items():
            lines.extend(SourceCheck(self._logins.source(source), probe).results(groups.resources(source)))
        return lines

    def require(self, targets: Sequence[LoginTarget]) -> List[CheckResult]:
        """The check of a run: any failure, a network error included, stops it (G12, J3)."""
        lines = self.check(targets)
        if not all(line.passed() for line in lines):
            raise LoginCheckFailed.from_results(lines)
        return lines


__all__ = [
    "CheckResult",
    "CheckState",
    "LoginCheck",
    "LoginCheckFailed",
    "LoginProbe",
    "LoginRejected",
    "LoginTarget",
    "SourceCheck",
    "SourceUnreachable",
    "TargetGroups",
]
