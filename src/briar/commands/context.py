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
from briar.storage import KNOWLEDGE_STORE_NAMES, KnowledgeRef, make_store


Handler = Callable[[argparse.Namespace], int]


class ContextCommand(Command):
    name = "context"
    help = "Store and read named local markdown blobs."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--store", default="file",
            choices=list(KNOWLEDGE_STORE_NAMES),
            help="Knowledge store backend (default: file)",
        )
        parser.add_argument(
            "--root", default="./knowledge",
            help="Local file root",
        )

        sub = parser.add_subparsers(dest="op", required=True)

        put = sub.add_parser("put", help="create or update a blob")
        put.add_argument("blob_name", help="e.g. knowledge:acme")
        put.add_argument("--content", help="inline content (or '-' for stdin)")
        put.add_argument("--from-file", help="read content from this path")
        put.add_argument(
            "--category", default="",
            help="explicit category (default: derived from blob_name prefix)",
        )

        gp = sub.add_parser("get", help="print the markdown body to stdout")
        gp.add_argument("blob_name")

        lst = sub.add_parser("list", help="list stored blobs")
        lst.add_argument(
            "--prefix", default="",
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
            raise CliError(
                "no content provided — pass --content '<text>', "
                "--from-file <path>, or pipe in via stdin"
            )
        return sys.stdin.read()

    @staticmethod
    def _ref_to_dict(ref: KnowledgeRef) -> dict:
        return {
            "name": ref.name,
            "category": ref.category,
            "byte_count": ref.byte_count,
            "updated_at": ref.updated_at,
            **ref.extra,
        }

    def _store(self, args: argparse.Namespace):
        return make_store(args.store, file_root=Path(args.root))

    def _put(self, args: argparse.Namespace) -> int:
        ref = self._store(args).put(
            args.blob_name,
            self._read_content(args),
            category=args.category,
        )
        render(self._ref_to_dict(ref), args.format)
        return 0

    def _get(self, args: argparse.Namespace) -> int:
        body = self._store(args).get(args.blob_name)
        if body is None:
            raise CliError(f"blob not found: {args.blob_name}")
        sys.stdout.write(body)
        if not body.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    def _list(self, args: argparse.Namespace) -> int:
        refs = self._store(args).list(prefix=args.prefix)
        items = [self._ref_to_dict(r) for r in refs]
        render(items, args.format, ["name", "category", "byte_count", "updated_at"])
        return 0

    def _delete(self, args: argparse.Namespace) -> int:
        ok = bool(args.yes) or confirm(
            f"Delete blob {args.blob_name} from store {args.store}? [y/N] "
        )
        if not ok:
            print("aborted")
            return 1
        removed = self._store(args).delete(args.blob_name)
        print(f"{'deleted' if removed else 'not found'} {args.blob_name}")
        return 0

    def _categories(self, args: argparse.Namespace) -> int:
        seen: Dict[str, int] = {}
        for ref in self._store(args).list():
            seen[ref.category] = seen.get(ref.category, 0) + 1
        items = [
            {"category": cat or "(none)", "blob_count": n}
            for cat, n in sorted(seen.items())
        ]
        render(items, args.format, ["category", "blob_count"])
        return 0
