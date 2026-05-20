"""`briar dashboard` — boot the read-only HTML dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path

from briar.commands.base import Command
from briar.dashboard.collectors import CollectorRegistry
from briar.dashboard.server import DashboardServer


class CommandDashboard(Command):
    name = "dashboard"
    help = "Serve a read-only HTML dashboard summarising the droplet state."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--host", default="0.0.0.0",
            help="bind address (default: 0.0.0.0 — public)",
        )
        parser.add_argument(
            "--port", type=int, default=8080,
            help="bind port (default: 8080)",
        )
        parser.add_argument(
            "--examples", default="./examples",
            help="directory of runbook YAMLs (default: ./examples)",
        )
        parser.add_argument(
            "--knowledge", default="./knowledge",
            help="knowledge-file root (default: ./knowledge)",
        )
        parser.add_argument(
            "--cron-file", default="/etc/cron.d/briar-scheduler",
            help="path to the cron entry (default: /etc/cron.d/briar-scheduler)",
        )
        parser.add_argument(
            "--log-file", default="/var/log/briar/scheduler.log",
            help="path to the scheduler log",
        )
        parser.add_argument(
            "--disk-path", default="/",
            help="filesystem path used for disk-usage stats",
        )
        parser.add_argument(
            "--repo-path", default=".",
            help="path of the deployed git checkout (for SHA/branch read-out)",
        )
        parser.add_argument(
            "--secrets-file", default="/etc/briar/secrets.env",
            help="path to secrets.env — names+lengths only, never values",
        )
        parser.add_argument(
            "--du-path", action="append", default=[],
            help="directory whose size to report (repeatable)",
        )
        parser.add_argument(
            "--once", action="store_true",
            help="render once to stdout and exit",
        )

    def run(self, args: argparse.Namespace) -> int:
        # Build the server first so the self-collector can read its
        # live counters.
        server = DashboardServer(
            collectors=[],  # populated below
            host=args.host,
            port=args.port,
        )
        du_paths = [Path(p) for p in (
            args.du_path or [args.repo_path, args.knowledge, "/var/log/briar"]
        )]
        server._collectors = CollectorRegistry.for_paths(  # noqa: SLF001
            examples_dir=Path(args.examples),
            knowledge_dir=Path(args.knowledge),
            cron_path=Path(args.cron_file),
            log_path=Path(args.log_file),
            disk_path=Path(args.disk_path),
            repo_path=Path(args.repo_path),
            secrets_path=Path(args.secrets_file),
            du_paths=du_paths,
            process_started_at=server.started_at,
            request_count_fn=server.request_count,
            last_render_ms_fn=server.last_render_ms,
        )
        if args.once:
            print(server.render_index())
            return 0
        server.serve()
        return 0
