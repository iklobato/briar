"""`briar auth` — interactive credential management.

Surface mirrors ``gh auth login`` / ``vault login`` / ``op signin``:
the thing you're logging into is the **positional target**, not a
named flag. One unified verb for both directions:

  briar auth login infisical                    # bootstrap a password manager
  briar auth login github-pat --company acme  # acquire vendor credentials
  briar auth login aws-sso --company acme --store infisical

The acquirer's ``destination_policy`` decides whether ``--store``
applies (vendor flows) or is forced to envfile (bootstrap flows that
populate the credentials needed to talk to a store).

Five subcommands:
  login    run an acquirer's interactive flow + persist
  logout   delete credentials a target's login would have written
  refresh  renew without re-prompting where the acquirer supports it
  list     enumerate credentials currently held in a store
  status   show one target × company bundle's coverage (set / missing)

Dispatch via the ``_ACTIONS`` map (one entry per subcommand) — same
table-driven dispatch as ``commands/secrets.py``."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
from typing import ClassVar, Dict, List

from briar.auth import AcquirerRegistry, CredentialExpired, Credentials, TerminalPromptIO
from briar.auth._acquirer import DestinationPolicy
from briar.commands.base import Command
from briar.credentials import CredentialStoreRegistry, make_credential_store
from briar.errors import CliError


log = logging.getLogger(__name__)


class CommandAuth(Command):
    name = "auth"
    help = "Acquire + persist credentials interactively (login / logout / list / status / refresh)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="auth_action", required=True)

        targets = list(AcquirerRegistry.kinds())
        store_kinds = list(CredentialStoreRegistry.kinds())
        default_store = _resolve_default_store(store_kinds)

        login = sub.add_parser(
            "login",
            help=(
                "Log into a target. Positional `target` is the thing you're authenticating to: "
                "a password manager (infisical) bootstraps a connection; "
                "a vendor (github-pat / aws-sso / jira-session) acquires credentials and stores them."
            ),
        )
        login.add_argument("target", choices=targets,
                           help="What to log into. See `briar auth login -h` for the registry.")
        login.add_argument("--company", default="", help="Per-company namespace (required for vendor targets).")
        login.add_argument(
            "--store", default=default_store, choices=store_kinds,
            help=(
                "Where to persist acquired credentials. Defaults to $BRIAR_DEFAULT_STORE then envfile. "
                "IGNORED for bootstrap targets (e.g. infisical) — those always persist locally."
            ),
        )

        logout = sub.add_parser("logout", help="Delete the credentials a target's login would have written.")
        logout.add_argument("target", choices=targets)
        logout.add_argument("--company", default="")
        logout.add_argument("--store", default=default_store, choices=store_kinds)
        logout.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")

        refresh = sub.add_parser(
            "refresh",
            help=(
                "Renew an OAuth / SSO bundle without re-prompting. Paste-based targets (PAT, app-password, "
                "Jira API token, Jira session cookie, Infisical machine identity) cannot refresh — re-run login."
            ),
        )
        refresh.add_argument("target", choices=targets)
        refresh.add_argument("--company", default="")
        refresh.add_argument("--store", default=default_store, choices=store_kinds)

        listing = sub.add_parser("list", help="Show which credential env-vars are populated (names only, no values).")
        listing.add_argument("--store", default=default_store, choices=store_kinds)
        listing.add_argument("--company", default="", help="Filter to one company (matches the company token in env-var names).")

        status = sub.add_parser("status", help="Show one target × company bundle's coverage (names + set/missing).")
        status.add_argument("target", choices=targets)
        status.add_argument("--company", default="")
        status.add_argument("--store", default=default_store, choices=store_kinds)

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
        acquirer = AcquirerRegistry.make(args.target)
        effective_store = _effective_store_kind(acquirer, requested=args.store)
        store = make_credential_store(effective_store)
        prompt = TerminalPromptIO()
        log.info("auth-login: target=%s company=%s store=%s policy=%s",
                 args.target, args.company or "(none)", effective_store,
                 type(acquirer).destination_policy.value)
        creds = acquirer.acquire(company=args.company, prompt=prompt)
        return _persist_and_report(creds, store=store, store_kind=effective_store)

    # ────────────────────────────── logout ─────────────────────────────

    @staticmethod
    def _logout(args: argparse.Namespace) -> int:
        acquirer = AcquirerRegistry.make(args.target)
        acquirer_cls = type(acquirer)
        names = acquirer_cls.writes(company=args.company)
        effective_store = _effective_store_kind(acquirer, requested=args.store)
        if not names:
            print(f"auth-logout: nothing to delete for target={args.target} company={args.company or '(none)'}")
            return 0
        if not args.yes:
            from briar.commands.base import confirm

            print(f"auth-logout: will delete {len(names)} env vars from store={effective_store}:")
            for n in names:
                print(f"  - {n}")
            if not confirm("proceed? "):
                print("aborted")
                return 1
        store = make_credential_store(effective_store)
        removed = 0
        for n in names:
            if store.delete(n):
                removed += 1
        print(f"auth-logout: removed {removed}/{len(names)} entries from store={effective_store}")
        return 0

    # ────────────────────────────── refresh ────────────────────────────

    @staticmethod
    def _refresh(args: argparse.Namespace) -> int:
        acquirer = AcquirerRegistry.make(args.target)
        effective_store = _effective_store_kind(acquirer, requested=args.store)
        store = make_credential_store(effective_store)

        # Reconstruct an "existing" bundle from the store so the
        # acquirer's refresh() can use it (OAuth refresh tokens,
        # expiry timestamps, etc.).
        acquirer_cls = type(acquirer)
        names = acquirer_cls.writes(company=args.company)
        existing = Credentials(
            provider_kind=args.target,
            entries={n: store.read(n) for n in names if store.read(n)},
        )
        new = acquirer.refresh(company=args.company, existing=existing)
        return _persist_and_report(new, store=store, store_kind=effective_store)

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
        acquirer = AcquirerRegistry.make(args.target)
        acquirer_cls = type(acquirer)
        names = acquirer_cls.writes(company=args.company)
        effective_store = _effective_store_kind(acquirer, requested=args.store)
        if not names:
            print(f"auth-status: target={args.target} writes nothing for company={args.company or '(none)'}")
            return 0
        store = make_credential_store(effective_store)
        print(f"auth-status: target={args.target} company={args.company or '(none)'} store={effective_store}")
        any_missing = False
        for n in names:
            val = store.read(n)
            mark = "ok  " if val else "MISS"
            print(f"  {mark}  {n}")
            if not val:
                any_missing = True
        return 1 if any_missing else 0


def _resolve_default_store(known_kinds: List[str]) -> str:
    """Default destination for ``--store``. Priority:
       1. ``$BRIAR_DEFAULT_STORE`` if it names a registered store
       2. ``envfile`` (always present, requires no setup)"""
    import os

    env = (os.environ.get("BRIAR_DEFAULT_STORE", "") or "").strip()
    if env and env in known_kinds:
        return env
    return "envfile"


def _effective_store_kind(acquirer, *, requested: str) -> str:
    """Honour the acquirer's destination policy. Bootstrap acquirers
    (Infisical machine identity, future Vault VAULT_ADDR/TOKEN) MUST
    persist locally — they can't store the credentials that describe
    how to reach their own store. Warn the operator if their
    ``--store`` choice gets overridden."""
    policy = type(acquirer).destination_policy
    if policy is DestinationPolicy.BOOTSTRAP_LOCAL and requested != "envfile":
        log.warning(
            "auth: target=%s is a bootstrap flow — forcing store=envfile "
            "(requested store=%s ignored; you can't store the credentials "
            "for a store inside that same store)",
            type(acquirer).kind,
            requested,
        )
        return "envfile"
    return requested


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
