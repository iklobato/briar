"""`briar context` — CRUD over named workspace blobs.

A blob holds arbitrary markdown — extracted knowledge, accumulated
memory, codified lessons, ad-hoc notes — keyed by `category:name`.
Backed by the pluggable `KnowledgeStore` Strategy (file or briar-api).

Examples:
    briar context put knowledge:acme --from-file knowledge/acme.md
    briar context put memory:reviewer-iklobato --content "focuses on typing"
    briar context list --store briar-api --prefix lessons:
    briar context get knowledge:acme --store briar-api
    briar context categories --store briar-api
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Dict, List

from briar.commands.base import Command, confirm
from briar.errors import CliError
from briar.formatting import render
from briar.http import ApiClient
from briar.storage import KNOWLEDGE_STORE_NAMES, KnowledgeRef, make_store


Handler = Callable[[ApiClient, argparse.Namespace], int]


def _read_content(args: argparse.Namespace) -> str:
    """Pick content from --content, --from-file, or stdin (in that order)."""
    namespace = vars(args)
    inline = namespace.get("content")
    if inline is not None:
        return inline if inline != "-" else sys.stdin.read()
    file_path = namespace.get("from_file")
    if file_path:
        return Path(file_path).read_text()
    if sys.stdin.isatty():
        raise CliError(
            "no content provided — pass --content '<text>', "
            "--from-file <path>, or pipe in via stdin"
        )
    return sys.stdin.read()


class ContextCommand(Command):
    name = "context"
    help = (
        "Store and read named workspace blobs (knowledge / memory / "
        "lessons / context). Backed by the pluggable KnowledgeStore."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        # Shared --store flag — every subcommand picks the backend
        parser.add_argument(
            "--store", default="file",
            choices=list(KNOWLEDGE_STORE_NAMES),
            help="Knowledge store backend (default: file)",
        )
        parser.add_argument(
            "--root", default="./knowledge",
            help="Local file root (only used when --store=file)",
        )

        sub = parser.add_subparsers(dest="op", required=True)

        # put
        put = sub.add_parser("put", help="create or update a blob")
        put.add_argument("blob_name", help="e.g. knowledge:acme")
        put.add_argument("--content", help="inline content (or '-' for stdin)")
        put.add_argument("--from-file", help="read content from this path")
        put.add_argument(
            "--category", default="",
            help="explicit category (default: derived from blob_name prefix)",
        )

        # get
        gp = sub.add_parser("get", help="print the markdown body to stdout")
        gp.add_argument("blob_name")

        # list
        lst = sub.add_parser("list", help="list stored blobs")
        lst.add_argument(
            "--prefix", default="",
            help="filter to names starting with this prefix",
        )

        # delete
        de = sub.add_parser("delete", help="remove a blob")
        de.add_argument("blob_name")
        de.add_argument("--yes", action="store_true")

        # categories
        sub.add_parser("categories", help="print distinct category prefixes")

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        handlers: Dict[str, Handler] = {
            "put": self._put,
            "get": self._get,
            "list": self._list,
            "delete": self._delete,
            "categories": self._categories,
        }
        return handlers[args.op](client, args)

    # ---- handlers --------------------------------------------------------

    def _store_for(
        self,
        client: ApiClient,
        args: argparse.Namespace,
    ):
        return make_store(
            args.store,
            client=client,
            file_root=Path(args.root),
        )

    def _put(self, client: ApiClient, args: argparse.Namespace) -> int:
        store = self._store_for(client, args)
        ref = store.put(
            args.blob_name,
            _read_content(args),
            category=args.category,
        )
        render(_ref_to_dict(ref), args.format)
        return 0

    def _get(self, client: ApiClient, args: argparse.Namespace) -> int:
        store = self._store_for(client, args)
        body = store.get(args.blob_name)
        if body is None:
            raise CliError(f"blob not found: {args.blob_name}")
        sys.stdout.write(body)
        if not body.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    def _list(self, client: ApiClient, args: argparse.Namespace) -> int:
        store = self._store_for(client, args)
        refs = store.list(prefix=args.prefix)
        items = [_ref_to_dict(r) for r in refs]
        render(items, args.format, ["name", "category", "byte_count", "updated_at"])
        return 0

    def _delete(self, client: ApiClient, args: argparse.Namespace) -> int:
        store = self._store_for(client, args)
        ok = bool(args.yes) or confirm(
            f"Delete blob {args.blob_name} from store {args.store}? [y/N] "
        )
        if not ok:
            print("aborted")
            return 1
        removed = store.delete(args.blob_name)
        print(f"{'deleted' if removed else 'not found'} {args.blob_name}")
        return 0

    def _categories(self, client: ApiClient, args: argparse.Namespace) -> int:
        store = self._store_for(client, args)
        seen: Dict[str, int] = {}
        for ref in store.list():
            seen[ref.category] = seen.get(ref.category, 0) + 1
        items = [
            {"category": cat or "(none)", "blob_count": n}
            for cat, n in sorted(seen.items())
        ]
        render(items, args.format, ["category", "blob_count"])
        return 0


def _ref_to_dict(ref: KnowledgeRef) -> dict:
    return {
        "name": ref.name,
        "category": ref.category,
        "byte_count": ref.byte_count,
        "updated_at": ref.updated_at,
        **ref.extra,
    }
