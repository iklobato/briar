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
            help="path to the scheduler log (default: /var/log/briar/scheduler.log)",
        )
        parser.add_argument(
            "--disk-path", default="/",
            help="filesystem path used for disk-usage stats (default: /)",
        )
        parser.add_argument(
            "--once", action="store_true",
            help="render once to stdout and exit (for ad-hoc / curl-less testing)",
        )

    def run(self, args: argparse.Namespace) -> int:
        collectors = CollectorRegistry.for_paths(
            examples_dir=Path(args.examples),
            knowledge_dir=Path(args.knowledge),
            cron_path=Path(args.cron_file),
            log_path=Path(args.log_file),
            disk_path=Path(args.disk_path),
        )
        server = DashboardServer(
            collectors=collectors,
            host=args.host,
            port=args.port,
        )
        if args.once:
            print(server.render_index())
            return 0
        server.serve()
        return 0
