"""`briar runbook` — drive multi-company applies from a single YAML."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable, Dict, List

from briar.commands.base import Command, confirm
from briar.errors import CliError
from briar.formatting import render
from briar.http import ApiClient
from briar.iac.runbook import (
    apply_runbook,
    destroy_runbook,
    extract_runbook,
    load_runbook_file,
    summarise_apply,
)


Handler = Callable[[ApiClient, argparse.Namespace], int]


class CommandRunbook(Command):
    name = "runbook"
    help = (
        "Apply / plan / destroy a multi-company runbook YAML. "
        "One file → many profiles × many scaffolds → one go."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(dest="op", required=True)

        ap = sub.add_parser("apply", help="reconcile every runbook in the file")
        ap.add_argument("file")
        ap.add_argument("--yes", action="store_true",
                        help="apply without printing the diff first")

        pl = sub.add_parser("plan", help="diff every runbook against live state")
        pl.add_argument("file")

        ds = sub.add_parser("destroy",
                            help="delete every resource declared in the file")
        ds.add_argument("file")
        ds.add_argument("--yes", action="store_true")

        ex = sub.add_parser(
            "extract",
            help="walk each company's `extract:` and write its knowledge_file",
        )
        ex.add_argument("file")

        sw = sub.add_parser(
            "sweep",
            help="run extract + apply for every *.yaml in a directory",
        )
        sw.add_argument("directory", help="folder containing runbook YAMLs")

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        handlers: Dict[str, Handler] = {
            "apply": self._apply,
            "plan": self._plan,
            "destroy": self._destroy,
            "extract": self._extract,
            "sweep": self._sweep,
        }
        # The runbook executor constructs its own ApiClient per company
        # (each company points at its own profile), so we ignore the
        # `client` arg the dispatcher hands us.
        return handlers[args.op](client, args)

    # ---- handlers --------------------------------------------------------

    def _apply(self, _client: ApiClient, args: argparse.Namespace) -> int:
        runbook = load_runbook_file(Path(args.file))
        if not args.yes:
            print("plan (dry-run):")
            plan_rows = apply_runbook(runbook, dry_run=True)
            _print_apply_rows(plan_rows, args.format)
            summary = summarise_apply(plan_rows)
            print(
                f"\nsummary: create={summary['create']} "
                f"update={summary['update']} noop={summary['noop']}"
            )
            if not confirm("apply? [y/N] "):
                print("aborted")
                return 1
        rows = apply_runbook(runbook, dry_run=False)
        _print_apply_rows(rows, args.format)
        return 0

    def _plan(self, _client: ApiClient, args: argparse.Namespace) -> int:
        runbook = load_runbook_file(Path(args.file))
        rows = apply_runbook(runbook, dry_run=True)
        _print_apply_rows(rows, args.format)
        summary = summarise_apply(rows)
        print(
            f"\nsummary: create={summary['create']} "
            f"update={summary['update']} noop={summary['noop']}"
        )
        return 0

    def _destroy(self, _client: ApiClient, args: argparse.Namespace) -> int:
        runbook = load_runbook_file(Path(args.file))
        ok = bool(args.yes) or confirm(
            f"destroy every resource in {args.file}? [y/N] "
        )
        if not ok:
            print("aborted")
            return 1
        rows = destroy_runbook(runbook)
        items = [
            {"company": c, "prefix": p, "kind": k, "name": n, "status": s}
            for c, p, k, n, s in rows
        ]
        render(items, args.format, ["company", "prefix", "kind", "name", "status"])
        return 0


    def _extract(self, _client: ApiClient, args: argparse.Namespace) -> int:
        runbook = load_runbook_file(Path(args.file))
        rows = extract_runbook(runbook)
        items = [
            {"company": c, "status": s, "output": o} for c, s, o in rows
        ]
        render(items, args.format, ["company", "status", "output"])
        return 0

    def _sweep(self, _client: ApiClient, args: argparse.Namespace) -> int:
        """Iterate every *.yaml in a directory and run extract → apply.
        Built for the headless scheduler cron — one entry point, no
        per-host shell logic."""
        root = Path(args.directory)
        if not root.is_dir():
            raise CliError(f"sweep: {root} is not a directory")
        files = sorted(p for p in root.glob("*.yaml") if p.is_file())
        if not files:
            print(f"sweep: no *.yaml under {root}")
            return 0
        exit_code = 0
        for path in files:
            print(f"--- {path.name} ---")
            try:
                runbook = load_runbook_file(path)
                extract_rows = extract_runbook(runbook)
                render(
                    [{"company": c, "status": s, "output": o}
                     for c, s, o in extract_rows],
                    args.format, ["company", "status", "output"],
                )
                apply_rows = apply_runbook(runbook, dry_run=False)
                _print_apply_rows(apply_rows, args.format)
            except Exception as exc:  # noqa: BLE001 — cron must not abort the loop
                print(f"FAILED {path.name}: {exc}")
                exit_code = 1
        return exit_code


def _print_apply_rows(rows: List[tuple], format_name: str) -> None:
    items = [
        {"company": c, "prefix": p, "kind": k, "name": n, "op": op, "id": uid}
        for c, p, k, n, op, uid in rows
    ]
    render(items, format_name, ["company", "prefix", "kind", "name", "op", "id"])
