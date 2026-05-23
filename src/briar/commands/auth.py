"""`briar auth` — interactive credential management.

Five subcommands, mirroring ``gh auth`` / ``aws sso login``:
  login    walk an acquirer's interactive flow + persist
  logout   delete credentials for a provider × company × store
  refresh  renew without re-prompting where the acquirer supports it
  list     enumerate which providers are logged in per company
  status   show one provider's bundle (names only — never values)

Dispatch via the ``_ACTIONS`` map (one entry per subcommand) — same
table-driven dispatch as ``commands/secrets.py``."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import ClassVar, Dict, List

from briar.auth import AcquirerRegistry, CredentialExpired, Credentials, TerminalPromptIO
from briar.commands.base import Command
from briar.credentials import CredentialStoreRegistry, make_credential_store
from briar.errors import CliError


log = logging.getLogger(__name__)


class CommandAuth(Command):
    name = "auth"
    help = "Acquire + persist credentials interactively (login / logout / list / status / refresh)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="auth_action", required=True)

        login = sub.add_parser("login", help="Run an acquirer's interactive flow and persist the result.")
        login.add_argument("--provider", required=True, choices=list(AcquirerRegistry.kinds()),
                           help="Which acquirer to run.")
        login.add_argument("--company", default="", help="Per-company namespace (required for most acquirers).")
        login.add_argument("--store", default="envfile", choices=list(CredentialStoreRegistry.kinds()),
                           help="Where to persist the acquired credentials (default: envfile).")

        logout = sub.add_parser("logout", help="Delete the credentials a given acquirer would have written.")
        logout.add_argument("--provider", required=True, choices=list(AcquirerRegistry.kinds()))
        logout.add_argument("--company", default="")
        logout.add_argument("--store", default="envfile", choices=list(CredentialStoreRegistry.kinds()))
        logout.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")

        refresh = sub.add_parser(
            "refresh",
            help="Renew an OAuth / SSO bundle without re-prompting. Paste-based acquirers (PAT, app-password, "
                 "Jira API token, Jira session cookie) cannot refresh — re-run login.",
        )
        refresh.add_argument("--provider", required=True, choices=list(AcquirerRegistry.kinds()))
        refresh.add_argument("--company", default="")
        refresh.add_argument("--store", default="envfile", choices=list(CredentialStoreRegistry.kinds()))

        listing = sub.add_parser("list", help="Show which provider env-vars are populated (names only, no values).")
        listing.add_argument("--store", default="envfile", choices=list(CredentialStoreRegistry.kinds()))
        listing.add_argument("--company", default="", help="Filter to one company (matches the company token in env-var names).")

        status = sub.add_parser("status", help="Show one provider × company bundle's coverage (names + set/missing).")
        status.add_argument("--provider", required=True, choices=list(AcquirerRegistry.kinds()))
        status.add_argument("--company", default="")
        status.add_argument("--store", default="envfile", choices=list(CredentialStoreRegistry.kinds()))

    _ACTIONS: ClassVar[Dict[str, str]] = {
        "login": "_login",
        "logout": "_logout",
        "refresh": "_refresh",
        "list": "_list",
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

    @staticmethod
    def _login(args: argparse.Namespace) -> int:
        acquirer = AcquirerRegistry.make(args.provider)
        store = make_credential_store(args.store)
        prompt = TerminalPromptIO()
        log.info("auth-login: provider=%s company=%s store=%s", args.provider, args.company or "(none)", args.store)
        creds = acquirer.acquire(company=args.company, prompt=prompt)
        return _persist_and_report(creds, store=store, store_kind=args.store)

    # ────────────────────────────── logout ─────────────────────────────

    @staticmethod
    def _logout(args: argparse.Namespace) -> int:
        acquirer_cls = type(AcquirerRegistry.make(args.provider))
        names = acquirer_cls.writes(company=args.company)
        if not names:
            print(f"auth-logout: nothing to delete for provider={args.provider} company={args.company or '(none)'}")
            return 0
        if not args.yes:
            from briar.commands.base import confirm

            print(f"auth-logout: will delete {len(names)} env vars from store={args.store}:")
            for n in names:
                print(f"  - {n}")
            if not confirm("proceed? "):
                print("aborted")
                return 1
        store = make_credential_store(args.store)
        removed = 0
        for n in names:
            if store.delete(n):
                removed += 1
        print(f"auth-logout: removed {removed}/{len(names)} entries from store={args.store}")
        return 0

    # ────────────────────────────── refresh ────────────────────────────

    @staticmethod
    def _refresh(args: argparse.Namespace) -> int:
        acquirer = AcquirerRegistry.make(args.provider)
        store = make_credential_store(args.store)

        # Reconstruct an "existing" bundle from the store so the
        # acquirer's refresh() can use it (OAuth refresh tokens,
        # expiry timestamps, etc.).
        acquirer_cls = type(acquirer)
        names = acquirer_cls.writes(company=args.company)
        existing = Credentials(
            provider_kind=args.provider,
            entries={n: store.read(n) for n in names if store.read(n)},
        )
        new = acquirer.refresh(company=args.company, existing=existing)
        return _persist_and_report(new, store=store, store_kind=args.store)

    # ────────────────────────────── list ───────────────────────────────

    @staticmethod
    def _list(args: argparse.Namespace) -> int:
        store = make_credential_store(args.store)
        names = store.list()
        if args.company:
            tok = args.company.upper().replace("-", "_")
            names = [n for n in names if f"_{tok}_" in f"_{n}_"]
        if not names:
            print(f"(no credentials in store={args.store}{' for company=' + args.company if args.company else ''})")
            return 0
        print(f"store={args.store}  {len(names)} entry(ies):")
        for n in names:
            print(f"  {n}")
        return 0

    # ────────────────────────────── status ─────────────────────────────

    @staticmethod
    def _status(args: argparse.Namespace) -> int:
        acquirer_cls = type(AcquirerRegistry.make(args.provider))
        names = acquirer_cls.writes(company=args.company)
        if not names:
            print(f"auth-status: provider={args.provider} writes nothing for company={args.company or '(none)'}")
            return 0
        store = make_credential_store(args.store)
        print(f"auth-status: provider={args.provider} company={args.company or '(none)'} store={args.store}")
        any_missing = False
        for n in names:
            val = store.read(n)
            mark = "ok  " if val else "MISS"
            print(f"  {mark}  {n}")
            if not val:
                any_missing = True
        return 1 if any_missing else 0


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
        if n in {f.split(':', 1)[0] for f in failures}:
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
        return 3
    return 0
