"""`briar auth`: interactive credential management.

Surface mirrors ``gh auth login``: the thing you're logging into is the
**positional target**, not a named flag.

  briar auth login github-pat --company acme   # acquire and save to secrets.env
  briar auth status jira-token --company acme  # which keys that target needs, set or missing
  briar auth status                            # every credential name held

Two operations since 2.0 (decision C15): `logout`, `refresh` and `list`
left, and so did `--cred-store`, since `secrets.env` is the only store
(L3, F1). Dispatch via the ``_ACTIONS`` map (one entry per operation)."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar, Dict, List

import typer

from briar.auth import AcquirerRegistry, CredentialExpired, Credentials, TerminalPromptIO
from briar.commands._enums import ExitCode
from briar.commands.base import Command, CommandOptions, RegistryChoice, Route
from briar.credentials import make_credential_store
from briar.errors import CliError

log = logging.getLogger(__name__)


class CommandAuth(Command):
    name = "auth"
    help = "Save credentials interactively and show which ones are set (login / status)."
    STORE = "envfile"

    OP_HELP = {
        "login": (
            "Log into a target. Positional `target` is the thing you're authenticating to: "
            "a password manager bootstraps a connection; "
            "a vendor (github-pat / aws-sso / jira-session) acquires credentials and stores them."
        ),
        "status": "With a target: the keys it needs and which are set. Without: every credential name held (names only, no values).",
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
        except CredentialExpired as exc:
            print(f"credential expired: {exc}")
            return 2

    # ────────────────────────────── login ──────────────────────────────

    def _login(self, args: argparse.Namespace) -> int:
        acquirer = AcquirerRegistry.make(args.target)
        log.info("auth-login: target=%s company=%s store=%s", args.target, args.company or "(none)", self.STORE)
        creds = acquirer.acquire(company=args.company, prompt=TerminalPromptIO())
        return _persist_and_report(creds, store=make_credential_store(self.STORE), store_kind=self.STORE)

    # ────────────────────────────── status ─────────────────────────────

    def _status(self, args: argparse.Namespace) -> int:
        if not args.target:
            return self._held(args.company)
        names = type(AcquirerRegistry.make(args.target)).writes(company=args.company)
        if not names:
            self._say(f"auth-status: target={args.target} writes nothing for company={args.company or '(none)'}")
            return 0
        store = make_credential_store(self.STORE)
        self._say(f"auth-status: target={args.target} company={args.company or '(none)'} store={self.STORE}")
        missing = [name for name in names if not store.read(name)]
        for name in names:
            self._say(f"  {'MISS' if name in missing else 'ok  '}  {name}")
        return 1 if missing else 0

    def _held(self, company: str) -> int:
        """`auth status` with no target: every credential name held, as 1.x `auth list` did."""
        names = make_credential_store(self.STORE).list()
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


def _persist_and_report(creds: Credentials, *, store, store_kind: str) -> int:
    """Common login/refresh tail: write every entry through the store
    and print a summary. Values are NEVER printed — only the key
    names and counts."""
    log.info("auth-persist: store=%s count=%d provider=%s", store_kind, len(creds.entries), creds.provider_kind)
    failures: List[str] = []
    for name, value in creds.entries.items():
        try:
            store.write(name, value)
        except Exception as exc:  # noqa: BLE001
            log.exception("auth-persist: write failed name=%s store=%s", name, store_kind)
            failures.append(f"{name}: {exc}")

    print(f"auth: persisted {len(creds.entries) - len(failures)}/{len(creds.entries)} entries to store={store_kind}")
    for n in creds.names:
        if n in {f.split(":", 1)[0] for f in failures}:
            print(f"  FAIL  {n}")
        else:
            print(f"  ok    {n}")
    if creds.expires_at:
        delta = creds.expires_at - datetime.now(tz=timezone.utc)
        days = delta.total_seconds() / 86400
        print(f"expires: {creds.expires_at.isoformat()} ({days:+.1f} days from now)")
    if failures:
        for f in failures:
            print(f"  reason: {f}")
        # ExitCode was folded to {OK, GENERAL_ERROR, USAGE_ERROR} in
        # Phase 8 — the granular 3 here was a leftover from the
        # pre-fold contract.
        return int(ExitCode.GENERAL_ERROR)
    return int(ExitCode.OK)


# ─── Options on Typer ──────────────────────────────────────────────

TARGET_HELP = "What to log into: " + ", ".join(AcquirerRegistry.kinds()) + "."  # read once, for help only


@dataclass(frozen=True)
class AuthLoginOptions(CommandOptions):
    ROUTE = Route(CommandAuth.name, "auth_action", "login")

    target: str = typer.Argument(..., metavar="TARGET", callback=RegistryChoice(AcquirerRegistry).check, help=TARGET_HELP)
    company: str = typer.Option("", "--company", help="Per-company namespace (required for vendor targets).")


@dataclass(frozen=True)
class AuthStatusOptions(CommandOptions):
    ROUTE = Route(CommandAuth.name, "auth_action", "status")

    target: str = typer.Argument("", metavar="[TARGET]", callback=RegistryChoice(AcquirerRegistry).check, help=TARGET_HELP + " Leave it out to list every credential name held.")
    company: str = typer.Option("", "--company", help="Per-company namespace; without a target, keeps only that company's names.")
