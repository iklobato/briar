"""`briar dashboard` — boot the read-only production-monitoring dashboard."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from briar.commands.base import Command
from briar.dashboard.collectors import CollectorRegistry, DashboardPaths, DashboardSelf
from briar.dashboard.server import DashboardServer

log = logging.getLogger(__name__)


class CommandDashboard(Command):
    name = "dashboard"
    help = "Serve a read-only HTML dashboard: host health, scheduler liveness + recent cycles, GitHub quota, connectivity."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--host",
            default="127.0.0.1",
            help="bind address (default: 127.0.0.1 — loopback only; " "pass `--host 0.0.0.0` to expose publicly, but verify firewall + auth first)",
        )
        parser.add_argument("--port", type=int, default=8080, help="bind port (default: 8080)")
        parser.add_argument("--examples", default="./examples", help="directory of runbook YAMLs (schedules view)")
        parser.add_argument("--log-file", default="/var/log/briar/scheduler.log", help="path to the scheduler log")
        parser.add_argument("--disk-path", default="/", help="filesystem path used for disk-usage stats")
        parser.add_argument("--repo-path", default=".", help="path of the deployed git checkout")
        parser.add_argument("--once", action="store_true", help="render once to stdout and exit")

    def run(self, args: argparse.Namespace) -> int:
        server = DashboardServer(host=args.host, port=args.port)
        paths = DashboardPaths(
            examples_dir=Path(args.examples),
            log_path=Path(args.log_file),
            disk_path=Path(args.disk_path),
            repo_path=Path(args.repo_path),
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
