"""Authentication verbs: login, logout, register, whoami."""

from __future__ import annotations

import argparse
import getpass
from typing import Any, Dict

from briar.commands.base import Command
from briar.errors import AuthError, CliError
from briar.fields import parse_fields
from briar.formatting import render_object
from briar.http import ApiClient


def _pick_workspace(me_payload: Any) -> str:
    """Pick the first membership's workspace id from /me/, or ''."""
    if type(me_payload) is not dict:
        return ""
    memberships = me_payload.get("memberships") or []
    if not memberships:
        return ""
    first = memberships[0]
    if type(first) is not dict:
        return ""
    ws = first.get("workspace")
    return str(ws) if ws else ""


class CommandLogin(Command):
    name = "login"
    help = "Log in with email + password and persist JWTs."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("email", nargs="?",
                            help="email (prompted if omitted)")
        parser.add_argument("--password",
                            help="password (prompted if omitted)")
        parser.add_argument("--workspace-id",
                            help="default workspace id to pin")

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        email = args.email or input("email: ").strip()
        password = args.password or getpass.getpass("password: ")
        if not email or not password:
            raise CliError("email and password are both required")

        payload = client.request(
            "POST", "/api/v1/auth/token/",
            {"email": email, "password": password},
        )
        access = payload.get("access", "") if type(payload) is dict else ""
        refresh = payload.get("refresh", "") if type(payload) is dict else ""
        if not access:
            raise AuthError("login response did not include `access`")

        creds = client.creds
        creds.access = access
        creds.refresh = refresh
        creds.email = email
        if args.workspace_id:
            creds.workspace = args.workspace_id
        client.store.save()

        me = client.request("GET", "/api/v1/me/")
        ws = creds.workspace
        if not ws:
            ws = _pick_workspace(me)
            creds.workspace = ws
            client.store.save()

        print(f"logged in as {email}")
        if ws:
            print(f"workspace: {ws}")
            return 0
        print(
            "no workspace pinned — run `briar workspace list` "
            "and `briar workspace use <id>`"
        )
        return 0


class CommandLogout(Command):
    name = "logout"
    help = "Forget local JWTs (clears the current profile)."

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        client.store.clear()
        print("logged out — tokens cleared")
        return 0


class CommandRegister(Command):
    name = "register"
    help = "Create a new Briar account via POST /api/v1/auth/register/."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--email", required=True)
        parser.add_argument("--password",
                            help="password (prompted if omitted)")
        parser.add_argument(
            "--field", action="append",
            help="additional registration fields, e.g. name=Joe",
        )

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        password = args.password or getpass.getpass("password: ")
        body: Dict[str, Any] = {"email": args.email, "password": password}
        body.update(parse_fields(args.field))
        payload = client.request("POST", "/api/v1/auth/register/", body)
        render_object(payload, args.format)
        return 0


class CommandWhoami(Command):
    name = "whoami"
    help = "Print the current user (/api/v1/me/)."

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        payload = client.request("GET", "/api/v1/me/")
        render_object(payload, args.format)
        return 0
