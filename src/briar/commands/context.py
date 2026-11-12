"""`briar context` — CRUD over local markdown knowledge blobs.

A blob holds arbitrary markdown — extracted knowledge, accumulated
memory, codified lessons, ad-hoc notes — keyed by `category:name`.
Backed by the local file `KnowledgeStore`."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

import typer

from briar.commands._enums import KnowledgeStoreChoice
from briar.commands.base import Command, CommandOptions, InheritingOptions, Route, confirm
from briar.errors import CliError
from briar.formatting import render
from briar.storage import KnowledgeRef, make_store

Handler = Callable[[argparse.Namespace], int]


class ContextCommand(Command):
    name = "context"
    help = "Store and read named local markdown blobs."
    OP_HELP = {
        "put": "create or update a blob",
        "get": "print the markdown body to stdout",
        "list": "list stored blobs",
        "delete": "remove a blob",
        "categories": "print distinct category prefixes",
    }

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
        if not body:
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
        ok = bool(args.yes) or confirm(f"Delete blob {args.blob_name} from store {args.store}? [y/N] ")
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
        items = [{"category": cat or "(none)", "blob_count": n} for cat, n in sorted(seen.items())]
        render(items, args.format, ["category", "blob_count"])
        return 0


# ─── Options on Typer ──────────────────────────────────────────────
# `--store` / `--root` work before the operation (`briar context --store postgres
# get x`, the 1.x position) and on it (`briar context get x --store postgres`).

STORE_HELP = "Knowledge store backend (default: file)"
ROOT_HELP = "Local file root (default: ./knowledge)"


@dataclass(frozen=True)
class ContextGroupOptions(CommandOptions):
    ROUTE = Route(ContextCommand.name)

    store: KnowledgeStoreChoice = typer.Option(KnowledgeStoreChoice.FILE, "--store", help=STORE_HELP)
    root: str = typer.Option("./knowledge", "--root", help=ROOT_HELP)


@dataclass(frozen=True)
class ContextOperationOptions(InheritingOptions):
    INHERITED = ("store", "root")

    store: Optional[KnowledgeStoreChoice] = typer.Option(None, "--store", help=STORE_HELP)
    root: Optional[str] = typer.Option(None, "--root", help=ROOT_HELP)


@dataclass(frozen=True)
class ContextPutOptions(ContextOperationOptions):
    ROUTE = Route(ContextCommand.name, "op", "put")

    blob_name: str = typer.Argument(..., help="e.g. knowledge:acme")
    content: Optional[str] = typer.Option(None, "--content", help="inline content (or '-' for stdin)")
    from_file: Optional[str] = typer.Option(None, "--from-file", help="read content from this path")
    category: str = typer.Option("", "--category", help="explicit category (default: derived from blob_name prefix)")


@dataclass(frozen=True)
class ContextGetOptions(ContextOperationOptions):
    ROUTE = Route(ContextCommand.name, "op", "get")

    blob_name: str = typer.Argument(...)


@dataclass(frozen=True)
class ContextListOptions(ContextOperationOptions):
    ROUTE = Route(ContextCommand.name, "op", "list")

    prefix: str = typer.Option("", "--prefix", help="filter to names starting with this prefix")


@dataclass(frozen=True)
class ContextDeleteOptions(ContextOperationOptions):
    ROUTE = Route(ContextCommand.name, "op", "delete")

    blob_name: str = typer.Argument(...)
    yes: bool = typer.Option(False, "--yes")


@dataclass(frozen=True)
class ContextCategoriesOptions(ContextOperationOptions):
    ROUTE = Route(ContextCommand.name, "op", "categories")
