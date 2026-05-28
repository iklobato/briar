"""`briar telemetry` — inspect and configure CLI telemetry.

Sub-ops (Strategy + Registry, same shape as `briar agent` / `briar plan`):

  * `status`       — print the current tier, install_id (hashed), and
                     where the configuration came from.
  * `preview`      — render the exact JSON event a typical command run
                     would emit. The event is NEVER sent.
  * `off`          — disable telemetry entirely. Persists to
                     ~/.config/briar/telemetry.json so the choice survives.
  * `errors-only`  — opt into Sentry crash reports only; usage analytics
                     stay off.
  * `full`         — opt into errors + usage analytics (the default).
  * `reset`        — regenerate the install_id (rotates the anonymous
                     identity that pairs your events together).

These ops never touch the network on their own; `off`/`errors-only`/`full`
just rewrite the local state file."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from typing import Dict

from briar._registry import build_registry
from briar.commands._enums import ExitCode
from briar.commands.base import Subcommand, SubcommandCommand
from briar.formatting import render
from briar.telemetry import TelemetryTier, active_config, preview_next_event, reset_install_id, save_tier
from briar.telemetry._config import config_dir, default_dsn, install_id_path, resolve, state_path

log = logging.getLogger(__name__)


# ─── TelemetryOp Strategy + Registry ────────────────────────────────


class TelemetryOp(Subcommand):
    """One `briar telemetry` subcommand."""


class StatusOp(TelemetryOp):
    name = "status"
    help = "Print the current telemetry tier and config source."

    def run(self, command, args: argparse.Namespace) -> int:
        cfg = active_config() or resolve()
        render(
            {
                "tier": cfg.tier.value,
                "source": cfg.source,
                "enabled": cfg.enabled,
                "install_id_hashed": cfg.hashed_install_id,
                "dsn_configured": bool(cfg.dsn),
                "default_dsn_set": bool(default_dsn()),
                "state_path": str(state_path()),
                "install_id_path": str(install_id_path()),
                "config_dir": str(config_dir()),
            },
            args.format,
        )
        return ExitCode.OK


class PreviewOp(TelemetryOp):
    name = "preview"
    help = "Print the exact JSON event that would be sent for the next command run."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--for-command",
            default="(preview)",
            dest="preview_command",
            help="Pretend command name to render the event for.",
        )

    def run(self, command, args: argparse.Namespace) -> int:
        event = preview_next_event(args.preview_command)
        sys.stdout.write(json.dumps(asdict(event), indent=2, sort_keys=True))
        sys.stdout.write("\n")
        return ExitCode.OK


class _SetTierOp(TelemetryOp):
    """Shared base for the three set-tier subcommands."""

    tier: TelemetryTier = TelemetryTier.FULL

    def run(self, command, args: argparse.Namespace) -> int:
        save_tier(self.tier, banner_shown=True)
        render({"tier": self.tier.value, "state_path": str(state_path())}, args.format)
        return ExitCode.OK


class OffOp(_SetTierOp):
    name = "off"
    help = "Disable telemetry entirely. Persists to the config file."
    tier = TelemetryTier.OFF


class ErrorsOnlyOp(_SetTierOp):
    name = "errors-only"
    help = "Opt into Sentry crash reports only; no usage analytics."
    tier = TelemetryTier.ERRORS_ONLY


class FullOp(_SetTierOp):
    name = "full"
    help = "Opt into errors + usage analytics (the default tier)."
    tier = TelemetryTier.FULL


class ResetOp(TelemetryOp):
    name = "reset"
    help = "Regenerate the install_id (rotate the anonymous identity)."

    def run(self, command, args: argparse.Namespace) -> int:
        new_id = reset_install_id()
        # We never print the raw id — only the hashed prefix, mirroring
        # what we'd actually send over the wire.
        from briar.telemetry._config import TelemetryConfig

        cfg = TelemetryConfig(tier=TelemetryTier.FULL, install_id=new_id)
        render(
            {
                "rotated": True,
                "install_id_hashed": cfg.hashed_install_id,
                "install_id_path": str(install_id_path()),
            },
            args.format,
        )
        return ExitCode.OK


TELEMETRY_OPS: Dict[str, TelemetryOp] = build_registry(
    (
        StatusOp(),
        PreviewOp(),
        OffOp(),
        ErrorsOnlyOp(),
        FullOp(),
        ResetOp(),
    ),
    kind="telemetry op",
)


# ─── CommandTelemetry ──────────────────────────────────────────────


class CommandTelemetry(SubcommandCommand):
    name = "telemetry"
    help = "Inspect and configure CLI telemetry (Sentry errors + usage analytics)."
    dest = "telemetry_op"
    op_noun = "telemetry op"
    ops = TELEMETRY_OPS
