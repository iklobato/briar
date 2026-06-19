"""`briar config show` — inspect the resolved project configuration.

Makes the precedence chain (CLI > env > .briar.toml > git inference >
default) debuggable: every setting's effective value and where it came
from. The table goes to stdout (honours `--format`); the config-file path
is a status line on stderr."""

from __future__ import annotations

import argparse
import sys

from briar.commands.base import Subcommand, SubcommandCommand


class ConfigShowOp(Subcommand):
    name = "show"
    help = "Print each setting's resolved value and its source."

    def run(self, command: "SubcommandCommand", args: argparse.Namespace) -> int:
        from briar.config import find_config_file, resolve_with_source
        from briar.formatting import render

        path = find_config_file()
        print(f"config file: {path if path else '(none found — using env / git / defaults)'}", file=sys.stderr)
        rows = [{"setting": r.setting, "value": r.value, "source": r.source} for r in resolve_with_source()]
        render(rows, args.format, ["setting", "value", "source"])
        return 0


class CommandConfig(SubcommandCommand):
    name = "config"
    help = "Inspect resolved project configuration (config show)."

    dest = "config_op"
    op_noun = "config op"
    ops = {"show": ConfigShowOp()}
