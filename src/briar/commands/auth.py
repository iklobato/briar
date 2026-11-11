"""`briar auth`: log into a source and see which logins are set.

The thing you log into is the **positional target**, a source name (spec 005, G9):

  briar auth login jira --company acme                            # asks what `[auth.jira] method` needs
  briar auth login jira --company acme --runbook runbooks/a.yaml  # the choices of a runbook company (G6)
  briar auth status jira --company acme                           # the names that login needs, set or missing
  briar auth status                                               # every credential name held

The values go to `secrets.env`, the only store (L3, G16); the choices (`method`,
`site`, `workspace`) come from the config, which briar never writes (H1). Two
operations since 2.0 (decision C15). AWS, GCP and Azure log in through their
own CLI (`aws`, `gcloud`, `az`; spec 005 I1 to I3), and briar saves nothing
for them."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Dict, List, Tuple

import typer

from briar.auth import TerminalPromptIO
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
        scope = LoginScope(self._company, str(path), f"companies.{self._company}.auth")
        return CompanyLogins(scope, companies[self._company].auth, os.environ)


class CommandAuth(Command):
    name = "auth"
    help = "Save credentials interactively and show which ones are set (login / status)."
    STORE = "envfile"

    OP_HELP = {
        "login": "Log into a source: asks what its `[auth.<source>]` method needs and saves it to secrets.env.",
        "status": "With a source: the names its login needs and which are set. Without: every credential name held (names only, no values).",
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
        log.info("auth-login: target=%s company=%s store=%s", args.target, args.company or "(none)", self.STORE)
        entries = AuthChoices(args.company, args.runbook).logins().source(args.target).login(TerminalPromptIO())
        if not entries:
            self._say(f"auth: {args.target} login done; its CLI keeps the login, briar saved nothing (L3)")
            return int(ExitCode.OK)
        return _persist_and_report(entries, store=EnvFileStore(), store_kind=self.STORE)

    # ────────────────────────────── status ─────────────────────────────

    def _status(self, args: argparse.Namespace) -> int:
        if not args.target:
            return self._held(args.company)
        login = AuthChoices(args.company, args.runbook).logins().source(args.target)
        lines = login.status_lines(EnvFileStore())
        self._say(f"auth-status: target={args.target} company={args.company or '(none)'} store={self.STORE}")
        for line in lines:
            self._say(f"  {'ok  ' if line.ok else 'MISS'}  {line.label}")
        hint = login.legacy_hint()
        if hint:
            self._say(f"  hint: {hint}")
        return 0 if all(line.ok for line in lines) else 1

    def _held(self, company: str) -> int:
        """`auth status` with no target: every credential name held, as 1.x `auth list` did."""
        names = EnvFileStore().list()
        token = company.upper().replace("-", "_")
        held = [name for name in names if not company or f"_{token}_" in f"_{name}_"]
        if not held:
            self._say(f"(no credentials in store={self.STORE}{' for company=' + company if company else ''})")
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

    target: str = typer.Argument("", metavar="[TARGET]", callback=RegistryChoice(LoginTargets).check, help=TARGET_HELP + " Leave it out to list every credential name held.")
    company: str = typer.Option("", "--company", help="The company; without a target, keeps only that company's names.")
    runbook: str = typer.Option("", "--runbook", metavar="FILE", help=RUNBOOK_HELP)
