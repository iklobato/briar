"""Environment health checks for `briar doctor`.

Inspects the Python/briar version, project config and git inference
offline, then checks the logins of the company live (spec 005, G14, G15,
J10 to J12): one line per configured source. Each check is a `Check`; the
command renders them and exits non-zero if any is a hard ``fail``.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _python_check() -> Check:
    v = platform.python_version()
    ok = sys.version_info >= (3, 10)
    return Check("python", OK if ok else FAIL, f"{v}{'' if ok else ' (briar needs >= 3.10)'}")


def _version_check() -> Check:
    from briar import __version__

    return Check("briar", OK, __version__)


def _config_check() -> Check:
    from briar.config import find_config_file

    path = find_config_file()
    if path is None:
        return Check("project config", WARN, "no .briar.toml found: run `briar init`")
    return Check("project config", OK, str(path))


def _git_check() -> Check:
    from briar.infer import git_remote_slug

    slug = git_remote_slug()
    if slug is None:
        return Check("git remote", WARN, "no git origin: run `briar init --owner OWNER --repo REPO`, or set [repo] in .briar.toml")
    return Check("git remote", OK, f"{slug[0]}/{slug[1]} (inferred)")


class LoginChecks:
    """The live login lines of the company in `.briar.toml` (J10 to J12): every configured
    source, with the resources `briar extract` would read. A failed check is `fail`
    (a run would stop), a network error is `warn` (G15)."""

    NAME = "logins"
    STATUS_OF = {"ok": OK, "failed": FAIL, "unreachable": WARN}
    FIX = "set its BRIAR_* secrets in the environment or in secrets.env (the README lists them)"

    def checks(self) -> List[Check]:
        from briar.auth.check import LoginCheck
        from briar.commands.extract import ExtractPlan
        from briar.config import load_project_config
        from briar.errors import CliError
        from briar.extract.login_probes import SourceProbes
        from briar.settings import SettingsResolver

        company = SettingsResolver.from_project(load_project_config()).settings().company or ""
        if not company:
            return [Check(self.NAME, WARN, "no company: set `company` in .briar.toml to check its logins")]
        try:
            plan = ExtractPlan.for_project(company)
        except CliError as error:
            return [Check(self.NAME, FAIL, str(error))]
        sources = plan.logins().configured()
        if not sources:
            return [Check(self.NAME, WARN, f"no login configured for {company}: {self.FIX}")]
        lines = LoginCheck(plan.logins()).check(SourceProbes(plan.logins()).for_sources(sources, plan.run_targets()))
        return [Check(f"{line.source} login", self.STATUS_OF[line.state.value], line.line()) for line in lines]


class RunbookChecks:
    """`doctor --runbooks DIR` (spec 003 part 4, X2): what `briar serve DIR` checks before
    its first cycle, without starting it: the runbooks load (the required keys), the
    logins of the scheduled extracts, and the read and write logins of each company with
    a `serve:` block. The model login and the sandbox have their own lines."""

    NAME = "runbooks"

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def checks(self) -> List[Check]:
        import os
        import shutil

        from briar.errors import CliError
        from briar.iac.runbook import RunbookScheduler
        from briar.serve.work import AgentSandbox, ServeCheck, ServeCompanies

        if not self._directory.is_dir():
            return [Check(self.NAME, FAIL, f"{self._directory} is not a directory")]
        try:
            companies = ServeCompanies(self._directory, os.environ).load()
            sandbox = AgentSandbox(platform.system(), shutil.which)
            # The scheduled extracts and the pipeline can check the same login: show it once.
            lines = list(dict.fromkeys([*RunbookScheduler(self._directory).login_results(), *ServeCheck(companies, os.environ, sandbox).lines()]))
        except CliError as error:
            return [Check(self.NAME, FAIL, str(error))]
        summary = Check(self.NAME, OK, f"{self._directory}: {len(companies)} companies with a `serve:` block")
        logins = [Check(f"{line.source} login", LoginChecks.STATUS_OF[line.state.value], line.line()) for line in lines]
        return [summary, *logins, *self._meeting_keys()]

    def _meeting_keys(self) -> List[Check]:
        """F11: one Fireflies key for several companies, one of them with no filter."""
        from briar.iac.runbook.meeting_keys import SharedMeetingKeys

        return [Check("meeting key", WARN, warning) for warning in SharedMeetingKeys(self._directory).warnings()]


class ModelLoginCheck:
    """The model login `briar serve` needs (spec 003, S3), offline: a name set, not tested."""

    NAME = "model login"

    def checks(self) -> List[Check]:
        import os

        from briar.serve.work import ModelLogin

        problem = ModelLogin(os.environ).problem()
        if problem:
            return [Check(self.NAME, WARN, f"{problem}; only `briar serve` needs it")]
        found = [name for name in ModelLogin.NAMES if os.environ.get(name, "").strip()]
        return [Check(self.NAME, OK, f"{found[0]} set")]


class AgentSandboxCheck:
    """The programs the sandbox of the agent of `briar serve` needs (spec 003, V14)."""

    NAME = "agent sandbox"

    def checks(self) -> List[Check]:
        import platform
        import shutil

        from briar.serve.work import AgentSandbox

        sandbox = AgentSandbox(platform.system(), shutil.which)
        problem = sandbox.problem()
        if problem:
            return [Check(self.NAME, WARN, f"{problem}; only `briar serve` needs it")]
        return [Check(self.NAME, OK, f"{sandbox.found()} found")]


_CHECKS = (
    _python_check,
    _version_check,
    _config_check,
    _git_check,
)


def run_checks() -> List[Check]:
    """Run every health check in display order: the offline ones, then the logins."""
    return [check() for check in _CHECKS] + ModelLoginCheck().checks() + AgentSandboxCheck().checks() + LoginChecks().checks()


def worst_status(checks: List[Check]) -> str:
    """The most severe status across `checks` (fail > warn > ok)."""
    statuses = {c.status for c in checks}
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return OK
