"""`briar completion <shell>` — print a shell-completion script.

Dependency-free: the script bakes in the command/subcommand/flag names by
introspecting the parser at generation time. See `briar.completion`."""

from __future__ import annotations

import argparse

from briar.commands.base import Command
from briar.completion import SUPPORTED_SHELLS, render


class CommandCompletion(Command):
    name = "completion"
    help = "Print a shell-completion script (bash/zsh)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "shell",
            choices=list(SUPPORTED_SHELLS),
            help='Shell to emit completion for. Install with: eval "$(briar completion bash)"',
        )

    def run(self, args: argparse.Namespace) -> int:
        from briar.commands import CommandRegistry

        # Introspect the full registry minus this command (completing
        # `briar completion` itself adds nothing useful).
        commands = {name: cmd for name, cmd in CommandRegistry.build().items() if name != self.name}
        print(render(args.shell, commands))
        return 0
