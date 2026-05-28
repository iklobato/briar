"""`briar journal` — inspect recorded sessions.

Three subcommands, table-driven dispatch (same pattern as
`commands/auth.py`, `commands/secrets.py`):

  list     enumerate stored sessions
  show     pretty-print one session as markdown
  export   write one session to a path (markdown or JSON)

Recording is internal — instrumented call-sites (e.g. the scaffold
composer) emit events via `briar.journal.record(...)`. The CLI surface
exposes the *read* side."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import ClassVar, Dict

from briar.commands.base import Command
from briar.errors import CliError
from briar.journal import JOURNAL_STORE_NAMES, make_journal_store
from briar.journal._render import render_markdown


log = logging.getLogger(__name__)


class CommandJournal(Command):
    name = "journal"
    help = "Inspect decision-journal sessions recorded by other briar commands."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="journal_action", required=True)

        listing = sub.add_parser("list", help="Enumerate stored sessions (newest first).")
        _add_store_args(listing)
        listing.add_argument(
            "--command",
            default="",
            dest="command_filter",
            help="Filter by command prefix (e.g. `scaffold.`).",
        )
        listing.add_argument("--limit", type=int, default=50)

        show = sub.add_parser("show", help="Pretty-print one session as markdown.")
        _add_store_args(show)
        show.add_argument("session_id")

        export = sub.add_parser("export", help="Write one session to a path.")
        _add_store_args(export)
        export.add_argument("session_id")
        # NOT `--format`: the global `--format` flag is extracted position-
        # independently in `briar.cli` and a subparser `--format` default
        # then clobbers it, making `--format json` unreachable here. `--as`
        # sidesteps the collision (dest is explicit because `as` is a
        # reserved word).
        export.add_argument(
            "--as",
            dest="export_format",
            choices=("markdown", "json"),
            default="markdown",
            help="Serialization for the exported session (default: markdown).",
        )
        export.add_argument("--out", default="-", help="Output path (`-` for stdout).")

    _ACTIONS: ClassVar[Dict[str, str]] = {
        "list": "_list",
        "show": "_show",
        "export": "_export",
    }

    def run(self, args: argparse.Namespace) -> int:
        action = getattr(args, "journal_action", "")
        method_name = self._ACTIONS.get(action)
        if not method_name:
            raise CliError(f"unknown journal action {action!r}")
        return getattr(self, method_name)(args)

    def _list(self, args: argparse.Namespace) -> int:
        store = _open_store(args)
        refs = store.list(command_prefix=args.command_filter, limit=args.limit)
        if not refs:
            print("(no sessions)")
            return 0
        for ref in refs:
            target = f" target={ref.target}" if ref.target else ""
            print(f"{ref.session_id}  {ref.command}{target}  decisions={ref.decision_count}  started={ref.started_at}")
        return 0

    def _show(self, args: argparse.Namespace) -> int:
        session = _open_store(args).get(args.session_id)
        if session is None:
            raise CliError(f"session {args.session_id!r} not found")
        print(render_markdown(session), end="")
        return 0

    def _export(self, args: argparse.Namespace) -> int:
        session = _open_store(args).get(args.session_id)
        if session is None:
            raise CliError(f"session {args.session_id!r} not found")
        text = json.dumps(session.to_dict(), indent=2) if args.export_format == "json" else render_markdown(session)
        if args.out == "-":
            print(text, end="" if args.export_format == "markdown" else "\n")
            return 0
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
        return 0


def _add_store_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--store",
        default="file",
        choices=JOURNAL_STORE_NAMES,
        help="Journal store backend (default: file).",
    )
    parser.add_argument(
        "--root",
        default="./journal",
        help="Root directory for the file-backed store (default: ./journal).",
    )


def _open_store(args: argparse.Namespace):
    return make_journal_store(args.store, file_root=Path(args.root))
