"""Generic CRUD command (Template Method).

`CommandResource` registers the standard `list/get/create/patch/delete`
subcommands for any flat `/api/v1/<resource>/` ViewSet. Subclasses set
class attributes (`name`, `base_path`, `columns`, `read_only`) and
optionally extend `_add_extras` / `_extra_handlers` to register custom
actions (e.g. `tasks cancel`).
"""

from __future__ import annotations

import argparse
from typing import Any, Callable, ClassVar, Dict, List

from briar.commands.base import Command, confirm
from briar.errors import CliError
from briar.fields import load_body, parse_fields
from briar.formatting import infer_columns, render, render_object
from briar.http import ApiClient


HandlerMap = Dict[str, Callable[[ApiClient, argparse.Namespace], int]]


class CommandResource(Command):
    base_path: ClassVar[str] = ""
    columns: ClassVar[List[str]] = []
    read_only: ClassVar[bool] = False

    # ---- argparse wiring -------------------------------------------------

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="op", required=True)

        self._add_list_parser(sub)
        self._add_get_parser(sub)
        if not self.read_only:
            self._add_write_parsers(sub)
        self._add_extras(sub)

    def _add_list_parser(self, sub: argparse._SubParsersAction) -> None:
        lst = sub.add_parser("list", help="list rows")
        lst.add_argument("--query", action="append", default=[],
                         help="filter as key=value (repeatable)")
        lst.add_argument("--limit", type=int,
                         help="page size (DRF `limit` query)")
        lst.add_argument("--offset", type=int,
                         help="page offset")
        lst.add_argument("--ordering",
                         help="`field` or `-field` (DRF ordering)")

    def _add_get_parser(self, sub: argparse._SubParsersAction) -> None:
        get = sub.add_parser("get", help="fetch a single row")
        get.add_argument("id")

    def _add_write_parsers(self, sub: argparse._SubParsersAction) -> None:
        cr = sub.add_parser("create", help="create a row")
        cr.add_argument("--from-file", help="JSON object with the body")
        cr.add_argument(
            "--field", action="append",
            help="key=value (repeatable; merged on top of --from-file)",
        )

        up = sub.add_parser("patch", help="partial update")
        up.add_argument("id")
        up.add_argument("--from-file")
        up.add_argument("--field", action="append")

        de = sub.add_parser("delete", help="delete a row")
        de.add_argument("id")
        de.add_argument("--yes", action="store_true",
                        help="skip the y/N prompt")

    def _add_extras(self, sub: argparse._SubParsersAction) -> None:
        """Subclasses override to register additional subcommands."""

    # ---- dispatch --------------------------------------------------------

    def run(
        self,
        client: ApiClient,
        args: argparse.Namespace,
    ) -> int:
        handlers: HandlerMap = {
            "list":   self._do_list,
            "get":    self._do_get,
            "create": self._do_create,
            "patch":  self._do_patch,
            "delete": self._do_delete,
        }
        handlers.update(self._extra_handlers())
        handler = handlers.get(args.op)
        if not handler:
            raise CliError(f"unknown subcommand: {args.op}")
        return handler(client, args)

    def _extra_handlers(self) -> HandlerMap:
        return {}

    # ---- default CRUD ----------------------------------------------------

    def _do_list(self, client: ApiClient, args: argparse.Namespace) -> int:
        query = parse_fields(args.query)
        for k, v in (("limit", args.limit),
                     ("offset", args.offset),
                     ("ordering", args.ordering)):
            if v not in (None, ""):
                query[k] = v
        items = client.list_all(self.base_path, query=query)
        cols = self.columns or infer_columns(items)
        render(items, args.format, cols)
        return 0

    def _do_get(self, client: ApiClient, args: argparse.Namespace) -> int:
        payload = client.request("GET", f"{self.base_path}{args.id}/")
        render_object(payload, args.format)
        return 0

    def _do_create(self, client: ApiClient, args: argparse.Namespace) -> int:
        body = load_body(args)
        payload = client.request("POST", self.base_path, body)
        render_object(payload, args.format)
        return 0

    def _do_patch(self, client: ApiClient, args: argparse.Namespace) -> int:
        body = load_body(args)
        payload = client.request(
            "PATCH", f"{self.base_path}{args.id}/", body,
        )
        render_object(payload, args.format)
        return 0

    def _do_delete(self, client: ApiClient, args: argparse.Namespace) -> int:
        ok = bool(args.yes) or confirm(
            f"Delete {self.name} {args.id}? [y/N] "
        )
        if not ok:
            print("aborted")
            return 1
        client.request("DELETE", f"{self.base_path}{args.id}/")
        print(f"deleted {self.name} {args.id}")
        return 0
