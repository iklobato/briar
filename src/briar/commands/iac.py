"""IaC commands: apply / plan / destroy / scaffold / export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from briar.commands.base import Command, confirm
from briar.errors import CliError
from briar.formatting import render
from briar.http import ApiClient
from briar.iac import (
    TEMPLATES,
    ConfigFile,
    destroy_all,
    reconcile,
)
from briar.iac.engine import summarise_ops
from briar.iac.reconcilers import RECONCILER_ORDER


_MANAGED_FIELDS = frozenset({
    "id", "created_at", "updated_at",
    "lineage_id", "version", "parent_version", "is_active", "bindings",
})


def _strip_managed(item: Any) -> Dict[str, Any]:
    if type(item) is not dict:
        return {"value": item}
    return {k: v for k, v in item.items() if k not in _MANAGED_FIELDS}


def _print_rows(rows: List[Tuple[str, str, str, str]], format_name: str) -> None:
    items = [
        {"kind": k, "name": n, "op": op, "id": uuid}
        for k, n, op, uuid in rows
    ]
    render(items, format_name, ["kind", "name", "op", "id"])


class CommandApply(Command):
    name = "apply"
    help = (
        "Reconcile a JSON config file: create/update each resource to "
        "match desired state."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("file", help="path to config.json")
        parser.add_argument(
            "--yes", action="store_true",
            help="apply without printing the diff first",
        )

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        cfg = ConfigFile.load(Path(args.file))
        if not args.yes:
            print("plan (dry-run):")
            plan_rows = reconcile(client, cfg, dry_run=True)
            _print_rows(plan_rows, args.format)
            summary = summarise_ops(plan_rows)
            print(
                f"\nsummary: create={summary['create']} "
                f"update={summary['update']} noop={summary['noop']}"
            )
            if not confirm("apply? [y/N] "):
                print("aborted")
                return 1
        rows = reconcile(client, cfg, dry_run=False)
        _print_rows(rows, args.format)
        return 0


class CommandPlan(Command):
    name = "plan"
    help = "Diff a JSON config file against the live workspace without writing."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("file")

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        cfg = ConfigFile.load(Path(args.file))
        rows = reconcile(client, cfg, dry_run=True)
        _print_rows(rows, args.format)
        summary = summarise_ops(rows)
        print(
            f"\nsummary: create={summary['create']} "
            f"update={summary['update']} noop={summary['noop']}"
        )
        return 0


class CommandDestroy(Command):
    name = "destroy"
    help = (
        "Delete every resource listed in a JSON config file "
        "(reverse dependency order)."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("file")
        parser.add_argument("--yes", action="store_true")

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        cfg = ConfigFile.load(Path(args.file))
        ok = bool(args.yes) or confirm(
            f"destroy resources declared in {args.file}? [y/N] "
        )
        if not ok:
            print("aborted")
            return 1
        rows = destroy_all(client, cfg)
        items = [
            {"kind": k, "name": n, "status": s} for k, n, s in rows
        ]
        render(items, args.format, ["kind", "name", "status"])
        return 0


class CommandScaffold(Command):
    name = "scaffold"
    help = "Generate a starter config file for a built-in template."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        sub = parser.add_subparsers(
            dest="template", required=True, metavar="TEMPLATE",
        )
        for name, tmpl in TEMPLATES.items():
            tp = sub.add_parser(name, help=tmpl.description)
            tp.add_argument(
                "--out", "-o", default="-",
                help="output path (default: stdout)",
            )
            tmpl.add_arguments(tp)

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        tmpl = TEMPLATES.get(args.template)
        if tmpl is None:
            raise CliError(f"unknown template: {args.template}")
        text = json.dumps(tmpl.build(args), indent=2)
        if args.out == "-":
            print(text)
            return 0
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
        return 0


class CommandExport(Command):
    name = "export"
    help = "Dump the current workspace's catalogue as a config file."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--out", "-o", default="-",
            help="output path (default: stdout)",
        )
        parser.add_argument(
            "--include", action="append", default=[],
            help="restrict to these section kinds (repeatable). "
                 "Defaults to all.",
        )

    def run(self, client: ApiClient, args: argparse.Namespace) -> int:
        wanted = (
            set(args.include) if args.include
            else {r.kind for r in RECONCILER_ORDER}
        )
        bundle: Dict[str, Any] = {"version": 1}
        for r in RECONCILER_ORDER:
            if r.kind not in wanted:
                continue
            items = client.list_all(r.base_path)
            bundle[r.kind] = [_strip_managed(it) for it in items]
        text = json.dumps(bundle, indent=2, default=str)
        if args.out == "-":
            print(text)
            return 0
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
        return 0
