"""`briar context` — CRUD over local markdown knowledge blobs.

A blob holds arbitrary markdown — extracted knowledge, accumulated
memory, codified lessons, ad-hoc notes — keyed by `category:name`.
Backed by the local file `KnowledgeStore`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Dict

from briar.commands.base import Command, confirm
from briar.errors import CliError
from briar.formatting import render
from briar.service import knowledge as knowledge_service
from briar.storage import KNOWLEDGE_STORE_NAMES

Handler = Callable[[argparse.Namespace], int]


class ContextCommand(Command):
    name = "context"
    help = "Store and read named local markdown blobs."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--store",
            default="file",
            choices=list(KNOWLEDGE_STORE_NAMES),
            help="Knowledge store backend (default: file)",
        )
        parser.add_argument(
            "--root",
            default="./knowledge",
            help="Local file root",
        )

        sub = parser.add_subparsers(dest="op", required=True)

        put = sub.add_parser("put", help="create or update a blob")
        put.add_argument("blob_name", help="e.g. knowledge:acme")
        put.add_argument("--content", help="inline content (or '-' for stdin)")
        put.add_argument("--from-file", help="read content from this path")
        put.add_argument(
            "--category",
            default="",
            help="explicit category (default: derived from blob_name prefix)",
        )

        gp = sub.add_parser("get", help="print the markdown body to stdout")
        gp.add_argument("blob_name")

        lst = sub.add_parser("list", help="list stored blobs")
        lst.add_argument(
            "--prefix",
            default="",
            help="filter to names starting with this prefix",
        )

        de = sub.add_parser("delete", help="remove a blob")
        de.add_argument("blob_name")
        de.add_argument("--yes", action="store_true")

        sub.add_parser("categories", help="print distinct category prefixes")

    def run(self, args: argparse.Namespace) -> int:
        handlers: Dict[str, Handler] = {
            "put": self._put,
            "get": self._get,
            "list": self._list,
            "delete": self._delete,
            "categories": self._categories,
        }
        return handlers[args.op](args)

    @staticmethod
    def _read_content(args: argparse.Namespace) -> str:
        ns = vars(args)
        inline = ns.get("content")
        if inline is not None:
            return inline if inline != "-" else sys.stdin.read()
        file_path = ns.get("from_file")
        if file_path:
            return Path(file_path).read_text()
        if sys.stdin.isatty():
            raise CliError("no content provided — pass --content '<text>', " "--from-file <path>, or pipe in via stdin")
        return sys.stdin.read()

    def _put(self, args: argparse.Namespace) -> int:
        outcome = knowledge_service.put_blob(
            blob_name=args.blob_name,
            content=self._read_content(args),
            category=args.category,
            store=args.store,
            root=args.root,
        )
        render(outcome.result["ref"], args.format)
        return 0

    def _get(self, args: argparse.Namespace) -> int:
        body = knowledge_service.get_blob(blob_name=args.blob_name, store=args.store, root=args.root)
        if body is None:
            raise CliError(f"blob not found: {args.blob_name}")
        sys.stdout.write(body)
        if not body.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    def _list(self, args: argparse.Namespace) -> int:
        items = knowledge_service.list_blobs(store=args.store, root=args.root, prefix=args.prefix)
        render(items, args.format, ["name", "category", "byte_count", "updated_at"])
        return 0

    def _delete(self, args: argparse.Namespace) -> int:
        ok = bool(args.yes) or confirm(f"Delete blob {args.blob_name} from store {args.store}? [y/N] ")
        if not ok:
            print("aborted")
            return 1
        outcome = knowledge_service.delete_blob(blob_name=args.blob_name, store=args.store, root=args.root)
        print(outcome.summary)
        return 0

    def _categories(self, args: argparse.Namespace) -> int:
        items = knowledge_service.categories(store=args.store, root=args.root)
        render(items, args.format, ["category", "blob_count"])
        return 0
