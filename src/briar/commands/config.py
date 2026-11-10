"""`briar config show` — inspect the resolved project configuration.

Makes the precedence chain (CLI > env > .briar.toml > git inference >
default) debuggable: every setting's effective value and where it came
from. The table goes to stdout (honours `--format`); the config-file path
is a status line on stderr."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from briar.commands.base import CommandOptions, Route, Subcommand, SubcommandCommand


class ConfigShowOp(Subcommand):
    name = "show"
    help = "Print each setting's resolved value and its source."

    def run(self, command: "SubcommandCommand", args: argparse.Namespace) -> int:
        from briar.config import find_config_file, load_project_config
        from briar.formatting import render
        from briar.settings import SettingsResolver

        path = find_config_file()
        print(f"config file: {path if path else '(none found — using env / git / defaults)'}", file=sys.stderr)
        rows = [item.row() for item in SettingsResolver.from_project(load_project_config()).resolved()]
        render(rows, args.format, ["setting", "value", "source"])
        return 0


class CommandConfig(SubcommandCommand):
    name = "config"
    help = "Inspect resolved project configuration (config show)."

    dest = "config_op"
    op_noun = "config op"
    ops = {"show": ConfigShowOp()}


@dataclass(frozen=True)
class ConfigShowOptions(CommandOptions):
    ROUTE = Route(CommandConfig.name, CommandConfig.dest, ConfigShowOp.name)
