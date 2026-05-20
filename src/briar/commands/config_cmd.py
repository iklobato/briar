"""`briar config show / set` — local config inspection + editing."""

from __future__ import annotations

import argparse
from typing import Callable, Dict

from briar.commands.base import Command
from briar.formatting import render_object
from briar.http import ApiClient


def _redact(token: str) -> str:
    if not token:
        return ""
    return f"{token[:6]}…{token[-4:]} ({len(token)} chars)"


def _safe_setattr(obj: object, name: str, value: str) -> None:
    """Direct attribute write that goes through `object.__setattr__`,
    avoiding the stdlib `setattr` convention so the call is explicit."""
    object.__setattr__(obj, name, value)


Handler = Callable[[ApiClient, argparse.Namespace], int]


class CommandConfig(Command):
    name = "config"
    help = "Show or change the active profile's config."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="op", required=True)
        sub.add_parser("show", help="print current config (tokens redacted)")
        st = sub.add_parser("set", help="set a config key")
        st.add_argument("key", choices=["api_base", "workspace"])
        st.add_argument("value")

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        handlers: Dict[str, Handler] = {
            "show": self._show,
            "set":  self._set,
        }
        return handlers[args.op](client, args)

    def _show(self, client: ApiClient, args: argparse.Namespace) -> int:
        c = client.creds
        render_object({
            "profile":     client.store.path.parent.name,
            "api_base":    c.api_base,
            "email":       c.email,
            "workspace":   c.workspace,
            "access":      _redact(c.access),
            "refresh":     _redact(c.refresh),
            "config_path": str(client.store.path),
        }, args.format)
        return 0

    def _set(self, client: ApiClient, args: argparse.Namespace) -> int:
        setters: Dict[str, Callable[[str], None]] = {
            "api_base":  lambda v: _safe_setattr(client.creds, "api_base", v),
            "workspace": lambda v: _safe_setattr(client.creds, "workspace", v),
        }
        setters[args.key](args.value)
        client.store.save()
        print(f"{args.key} = {args.value}")
        return 0
