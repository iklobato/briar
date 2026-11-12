"""`briar auth`: log into a source and see which logins are set.

The thing you log into is the **positional target**, a source name (spec 005, G9):

  briar auth login jira --company acme                            # asks what `[auth.jira] method` needs
  briar auth login jira --company acme --runbook runbooks/a.yaml  # the choices of a runbook company (G6)
  briar auth status jira --company acme                           # checks that login live
  briar auth status --company acme                                # checks every configured login
  briar auth status                                               # every credential name held

The values go to `secrets.env`, the only store (L3, G16); the choices (`method`,
`site`, `workspace`) come from the config, which briar never writes (H1). Two
operations since 2.0 (decision C15). AWS, GCP and Azure log in through their
own CLI (`aws`, `gcloud`, `az`; spec 005 I1 to I3), and briar saves nothing
for them. `login` checks the login ("who am I") before it saves (J9); `status`
checks it live with the resources the run reads (J11, J13, J14)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Dict, List, Sequence, Tuple

import typer

from briar.auth import TerminalPromptIO
from briar.auth.check import CheckResult, CheckState, LoginCheck, LoginTarget
from briar.auth.config import AuthTable
from briar.auth.logins import CompanyLogins, LoginScope
from briar.commands._enums import ExitCode
from briar.commands.base import Command, CommandOptions, RegistryChoice, Route
from briar.credentials import EnvFileStore
from briar.errors import CliError

log = logging.getLogger(__name__)


class LoginTargets:
    """What `briar auth` logs into: the sources of `briar.auth.logins`."""

    @classmethod
    def kinds(cls) -> Tuple[str, ...]:
        return tuple(CompanyLogins.SOURCES)


class AuthChoices:
    """The login choices of one company: `[auth]` of `.briar.toml`, or
    `companies.<company>.auth` of the runbook named by `--runbook` (G6)."""

    def __init__(self, company: str, runbook: str) -> None:
        self._company = company
        self._runbook = runbook

    def logins(self) -> CompanyLogins:
        if self._runbook:
            return self._from_runbook(Path(self._runbook))
        return self._from_project()

    def run_targets(self, logins: CompanyLogins) -> List[LoginTarget]:
        """What the run of this company reads (J11): its runbook tasks with `--runbook`, else
        `briar extract` in this project."""
        from briar.commands.extract import ExtractConfig, ExtractPlan

        if self._runbook:
            return self._runbook_targets(logins)
        config = ExtractConfig.from_project()
        config.check()
        return ExtractPlan(config, config.command_line(self._company), logins).run_targets()

    def _runbook_targets(self, logins: CompanyLogins) -> List[LoginTarget]:
        from briar.extract import EXTRACTORS
        from briar.iac.runbook.executor import RunbookLoader, RunbookLoginCheck, RunbookSchedules

        runbook_file = RunbookLoader.load(Path(self._runbook))
        schedules = RunbookSchedules.for_company(runbook_file.companies[self._company])
        return RunbookLoginCheck(runbook_file, EXTRACTORS).targets(logins, schedules)

    def _from_project(self) -> CompanyLogins:
        from briar.config import find_config_file, load_project_config

        path = find_config_file()
        origin = str(path) if path else ".briar.toml"
        return CompanyLogins(LoginScope(self._company, origin), AuthTable(load_project_config(), origin).block(), os.environ)

    def _from_runbook(self, path: Path) -> CompanyLogins:
        from briar.iac.runbook.executor import RunbookLoader

        companies = RunbookLoader.load(path).companies
        if self._company not in companies:
            raise CliError(f"company {self._company!r} is not in {path} (companies: {', '.join(companies)})")
        scope = LoginScope(self._company, str(path), f"companies.{self._company}.auth", runbook=str(path))
        return CompanyLogins(scope, companies[self._company].auth, os.environ)


class CommandAuth(Command):
    name = "auth"
    help = "Save credentials interactively and show which ones are set (login / status)."
    STORE = "envfile"

    OP_HELP = {
        "login": "Log into a source: asks what its `[auth.<source>]` method needs, checks it, and saves it to secrets.env.",
        "status": "Checks the login of a source live, or every configured source of the company. With no company: every credential name held.",
    }

    _ACTIONS: ClassVar[Dict[str, str]] = {
        "login": "_login",
        "status": "_status",
    }

    def run(self, args: argparse.Namespace) -> int:
        handler_name = self._ACTIONS.get(args.auth_action)
        if handler_name is None:
            known = ", ".join(sorted(self._ACTIONS.keys()))
            print(f"unknown auth action: {args.auth_action} (known: {known})")
            return 1
        try:
            return getattr(self, handler_name)(args)
        except CliError as exc:
            print(f"error: {exc}")
            return 1

    # ────────────────────────────── login ──────────────────────────────

    def _login(self, args: argparse.Namespace) -> int:
        from briar.extract.login_probes import SourceProbes

        log.info("auth-login: target=%s company=%s store=%s", args.target, args.company or "(none)", self.STORE)
        logins = AuthChoices(args.company, args.runbook).logins()
        entries = logins.source(args.target).login(TerminalPromptIO())
        # J9: "who am I" with the values just entered, before anything is saved.
        checked = logins.with_entries(entries)
        for line in LoginCheck(checked).require([LoginTarget(SourceProbes(checked).target(args.target).probe)]):
            self._say(f"auth: {line.line()}")
        if not entries:
            self._say(f"auth: {args.target} login done; its CLI keeps the login, briar saved nothing (L3)")
            return int(ExitCode.OK)
        return _persist_and_report(entries, store=EnvFileStore(), store_kind=self.STORE)

    # ────────────────────────────── status ─────────────────────────────

    def _status(self, args: argparse.Namespace) -> int:
        from briar.extract.login_probes import SourceProbes

        if not args.target and not args.company:
            return self._held()
        choices = AuthChoices(args.company, args.runbook)
        logins = choices.logins()
        sources = [args.target] if args.target else logins.configured()
        self._say(f"auth-status: company={args.company} sources={', '.join(sources) or '(none configured)'}")
        lines = LoginCheck(logins).check(SourceProbes(logins).for_sources(sources, choices.run_targets(logins)))
        return self._report(lines)

    def _report(self, lines: Sequence[CheckResult]) -> int:
        """J14: a network error is shown and exits 0, as in `doctor`; any other failure exits 1."""
        for line in lines:
            self._say(f"  {line.state.value:<11}  {line.line()}")
        failed = any(line.state is CheckState.FAILED for line in lines)
        return int(ExitCode.GENERAL_ERROR) if failed else int(ExitCode.OK)

    def _held(self) -> int:
        """`auth status` with no target and no company: every credential name held."""
        held = EnvFileStore().list()
        if not held:
            self._say(f"(no credentials in store={self.STORE})")
            return 0
        self._say(f"store={self.STORE}  {len(held)} entry(ies):")
        for name in held:
            self._say(f"  {name}")
        return 0

    def _say(self, line: str) -> None:
        """Command output (stdout, read at call time so a test can capture it)."""
        sys.stdout.write(f"{line}\n")


def _persist_and_report(entries: Dict[str, str], *, store, store_kind: str) -> int:
    """Common login tail: write every entry through the store
    and print a summary. Values are NEVER printed — only the key
    names and counts."""
    log.info("auth-persist: store=%s count=%d", store_kind, len(entries))
    failures: List[str] = []
    for name, value in entries.items():
        try:
            store.write(name, value)
        except Exception as exc:  # noqa: BLE001
            log.exception("auth-persist: write failed name=%s store=%s", name, store_kind)
            failures.append(f"{name}: {exc}")

    sys.stdout.write(f"auth: persisted {len(entries) - len(failures)}/{len(entries)} entries to store={store_kind}\n")
    for n in entries:
        if n in {f.split(":", 1)[0] for f in failures}:
            sys.stdout.write(f"  FAIL  {n}\n")
        else:
            sys.stdout.write(f"  ok    {n}\n")
    if failures:
        for f in failures:
            sys.stdout.write(f"  reason: {f}\n")
        # ExitCode was folded to {OK, GENERAL_ERROR, USAGE_ERROR} in
        # Phase 8 — the granular 3 here was a leftover from the
        # pre-fold contract.
        return int(ExitCode.GENERAL_ERROR)
    return int(ExitCode.OK)


# ─── Options on Typer ──────────────────────────────────────────────

TARGET_HELP = "What to log into: " + ", ".join(LoginTargets.kinds()) + "."  # read once, for help only
RUNBOOK_HELP = "Read the login choices from `companies.<company>.auth` of this runbook instead of `.briar.toml`."


@dataclass(frozen=True)
class AuthLoginOptions(CommandOptions):
    ROUTE = Route(CommandAuth.name, "auth_action", "login")

    target: str = typer.Argument(..., metavar="TARGET", callback=RegistryChoice(LoginTargets).check, help=TARGET_HELP)
    company: str = typer.Option("", "--company", help="The company the login is for (default: `company` in .briar.toml).")
    runbook: str = typer.Option("", "--runbook", metavar="FILE", help=RUNBOOK_HELP)


@dataclass(frozen=True)
class AuthStatusOptions(CommandOptions):
    ROUTE = Route(CommandAuth.name, "auth_action", "status")

    target: str = typer.Argument(
        "", metavar="[TARGET]", callback=RegistryChoice(LoginTargets).check, help=TARGET_HELP + " Leave it out to check every configured login."
    )
    company: str = typer.Option("", "--company", help="The company to check; with no company and no target, lists every credential name held.")
    runbook: str = typer.Option("", "--runbook", metavar="FILE", help=RUNBOOK_HELP)
