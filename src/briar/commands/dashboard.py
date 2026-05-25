"""`briar dashboard` — boot the read-only HTML dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path

from briar.commands.base import Command
from briar.dashboard.collectors import CollectorRegistry, DashboardPaths, DashboardSelf
from briar.dashboard.server import DashboardServer
from briar.env_vars import CredEnv
from briar.journal import JOURNAL_STORE_NAMES, make_journal_store
from briar.storage import KNOWLEDGE_STORE_NAMES, make_store


class CommandDashboard(Command):
    name = "dashboard"
    help = "Serve a read-only HTML dashboard summarising the droplet state."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--host", default="0.0.0.0", help="bind address (default: 0.0.0.0 — public)")
        parser.add_argument("--port", type=int, default=8080, help="bind port (default: 8080)")
        parser.add_argument("--examples", default="./examples", help="directory of runbook YAMLs")
        parser.add_argument(
            "--knowledge-store",
            default="",
            choices=[""] + list(KNOWLEDGE_STORE_NAMES),
            help="which KnowledgeStore to read from (default: postgres if BRIAR_DATABASE_URL is set, else file)",
        )
        parser.add_argument("--knowledge", default="./knowledge", help="knowledge file root (only used when --knowledge-store=file)")
        parser.add_argument("--log-file", default="/var/log/briar/scheduler.log", help="path to the scheduler log")
        parser.add_argument("--disk-path", default="/", help="filesystem path used for disk-usage stats")
        parser.add_argument("--repo-path", default=".", help="path of the deployed git checkout")
        parser.add_argument("--secrets-file", default="/etc/briar/secrets.env", help="secrets.env path (names+lengths only)")
        parser.add_argument("--du-path", action="append", default=[], help="directory whose size to report (repeatable)")
        parser.add_argument(
            "--journal-store",
            default="file",
            choices=list(JOURNAL_STORE_NAMES),
            help="JournalStore backend (default: file)",
        )
        parser.add_argument(
            "--journal-root",
            default="./journal",
            help="Journal file root (only used when --journal-store=file)",
        )
        parser.add_argument("--once", action="store_true", help="render once to stdout and exit")

    def run(self, args: argparse.Namespace) -> int:
        store_name = args.knowledge_store or ("postgres" if CredEnv.BRIAR_DATABASE_URL.read() else "file")
        knowledge_store = make_store(store_name, file_root=Path(args.knowledge))

        # Journal store is best-effort: a missing root is fine, the
        # affected collectors render an `_error` panel instead of
        # crashing the page.
        journal_store = None
        try:
            journal_store = make_journal_store(
                args.journal_store,
                file_root=Path(args.journal_root),
            )
        except Exception:  # noqa: BLE001
            journal_store = None

        server = DashboardServer(host=args.host, port=args.port)
        paths = DashboardPaths(
            examples_dir=Path(args.examples),
            knowledge_store=knowledge_store,
            log_path=Path(args.log_file),
            disk_path=Path(args.disk_path),
            repo_path=Path(args.repo_path),
            secrets_path=Path(args.secrets_file),
            du_paths=[Path(p) for p in (args.du_path or [args.repo_path, args.knowledge, "/var/log/briar"])],
            journal_store=journal_store,
        )
        dash = DashboardSelf(
            started_at=server.started_at,
            request_count_fn=server.request_count,
            last_render_ms_fn=server.last_render_ms,
        )
        server.set_collectors(CollectorRegistry.from_paths(paths, dash))
        if args.once:
            print(server.render_index())
            return 0
        server.serve()
        return 0
