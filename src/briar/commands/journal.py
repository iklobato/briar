"""`briar journal` — inspect recorded sessions.

Three subcommands, table-driven dispatch (same pattern as
`commands/auth.py`, `commands/secrets.py`):

  list     enumerate stored sessions
  show     pretty-print one session as markdown
  export   write one session to a path (markdown or JSON)

Recording is internal — instrumented call-sites (e.g. `plan run`)
emit events via `briar.journal.record(...)`. The CLI surface
exposes the *read* side."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import ClassVar, Dict

import typer

from briar.commands._enums import JournalStoreChoice
from briar.commands.base import Command, CommandOptions, Route
from briar.errors import CliError
from briar.journal import make_journal_store
from briar.journal._render import render_markdown

log = logging.getLogger(__name__)


class CommandJournal(Command):
    name = "journal"
    help = "Inspect decision-journal sessions recorded by other briar commands."

    OP_HELP = {
        "list": "Enumerate stored sessions (newest first).",
        "show": "Pretty-print one session as markdown.",
        "export": "Write one session to a path.",
    }

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
        print(f"wrote {args.out}", file=sys.stderr)
        return 0


def _open_store(args: argparse.Namespace):
    return make_journal_store(args.journal_store, file_root=Path(args.journal_root))


# ─── Options on Typer ──────────────────────────────────────────────

DEFAULT_LIST_LIMIT = 50


class ExportFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


@dataclass(frozen=True)
class JournalStoreOptions(CommandOptions):
    """`--store` / `--root` of every journal operation. Their dests are not `store` /
    `root`: those settings name the knowledge store, not the journal."""

    journal_store: JournalStoreChoice = typer.Option(JournalStoreChoice.FILE, "--store", help="Journal store backend (default: file).")
    journal_root: str = typer.Option("./journal", "--root", help="Root directory for the file-backed store (default: ./journal).")


@dataclass(frozen=True)
class JournalListOptions(JournalStoreOptions):
    ROUTE = Route(CommandJournal.name, "journal_action", "list")

    command_filter: str = typer.Option("", "--command", help="Filter by command prefix (e.g. `plan.`).")
    limit: int = typer.Option(DEFAULT_LIST_LIMIT, "--limit")


@dataclass(frozen=True)
class JournalShowOptions(JournalStoreOptions):
    ROUTE = Route(CommandJournal.name, "journal_action", "show")

    session_id: str = typer.Argument(...)


@dataclass(frozen=True)
class JournalExportOptions(JournalStoreOptions):
    ROUTE = Route(CommandJournal.name, "journal_action", "export")

    session_id: str = typer.Argument(...)
    # Not `--format`: that is the global output flag, read anywhere on the line.
    export_format: ExportFormat = typer.Option(ExportFormat.MARKDOWN, "--as", help="Serialization for the exported session (default: markdown).")
    out: str = typer.Option("-", "--out", help="Output path (`-` for stdout).")
