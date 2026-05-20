"""`briar version` — local client version."""

from __future__ import annotations

import argparse

from briar import __version__
from briar.commands.base import Command


class CommandVersion(Command):
    name = "version"
    help = "Print client version."

    def run(self, args: argparse.Namespace) -> int:
        print(f"briar-cli {__version__}")
        return 0
